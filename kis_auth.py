from __future__ import annotations

"""kis_auth.py (PairBot)

KIS 인증 유틸(토큰/웹소켓 approval_key/hashkey) — kis_orb_vwap_bot 스타일로 맞춤.

- 캐시 파일(.kis_token_cache.json)을 사용해 토큰 재발급 폭주를 방지
- KIS 레이트리밋(EGW00133: 1분당 1회)에 대비해 최소 호출 간격을 둠
- WebSocket approval_key도 동일하게 캐시/최소간격 적용

NOTE
- 이 구현은 **requests 기반**(명세 준수)으로 작성했습니다.
- 실전 운영에서는 .env/권한(600) 등 보안에 유의하세요.
"""

import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests


@dataclass
class AuthToken:
    access_token: str
    token_type: str
    expires_in: int


@dataclass
class ApprovalKey:
    approval_key: str
    expires_in: int


class KISAuth:
    def __init__(self, base_url: str, app_key: str, app_secret: str, logger) -> None:
        self.base_url = base_url
        self.app_key = app_key
        self.app_secret = app_secret
        self.logger = logger

        self.token: Optional[AuthToken] = None
        self.approval: Optional[ApprovalKey] = None

        self._token_expire_at = 0.0
        self._approval_expire_at = 0.0

        self._cache_path = Path(os.environ.get("KIS_TOKEN_CACHE_PATH", ".kis_token_cache.json"))
        self._token_min_interval_sec = float(os.environ.get("KIS_TOKEN_MIN_INTERVAL_SEC", "62"))
        self._approval_min_interval_sec = float(os.environ.get("KIS_APPROVAL_MIN_INTERVAL_SEC", "62"))
        self._token_next_allowed_at = 0.0
        self._approval_next_allowed_at = 0.0

        self._load_token_cache()

    def _now(self) -> float:
        return time.time()

    def _wait_rate_limit(self, kind: str) -> None:
        if kind == "token":
            wait = self._token_next_allowed_at - self._now()
            if wait > 0:
                time.sleep(wait)
            self._token_next_allowed_at = self._now() + self._token_min_interval_sec
        else:
            wait = self._approval_next_allowed_at - self._now()
            if wait > 0:
                time.sleep(wait)
            self._approval_next_allowed_at = self._now() + self._approval_min_interval_sec

    def _load_token_cache(self) -> None:
        try:
            if not self._cache_path.exists():
                return
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
            token = AuthToken(
                access_token=str(data.get("access_token", "") or ""),
                token_type=str(data.get("token_type", "") or "Bearer"),
                expires_in=int(data.get("expires_in", 0) or 0),
            )
            exp = float(data.get("expire_at_epoch", 0.0) or 0.0)
            if token.access_token and exp > (self._now() + 60):
                self.token = token
                self._token_expire_at = exp
                self.logger.info("auth token restored from cache")
        except Exception:
            return

    def _save_token_cache(self) -> None:
        if not self.token:
            return
        payload = {
            "access_token": self.token.access_token,
            "token_type": self.token.token_type,
            "expires_in": int(self.token.expires_in),
            "expire_at_epoch": float(self._token_expire_at),
            "cached_at_epoch": self._now(),
        }
        try:
            self._cache_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
            try:
                os.chmod(self._cache_path, 0o600)
            except Exception:
                pass
        except Exception:
            return

    def fetch_token(self, force: bool = False) -> AuthToken:
        if (not force) and self.token and self._now() < (self._token_expire_at - 60):
            return self.token

        self._wait_rate_limit("token")

        url = f"{self.base_url}/oauth2/tokenP"
        payload = {"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret}

        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(1, 6):
            try:
                resp = requests.post(url, json=payload, timeout=10)
                # KIS는 403/429에 EGW00133이 같이 나오는 경우가 있어 메시지까지 포함해 판단
                if resp.status_code != 200:
                    msg = resp.text
                    is_rate = resp.status_code in (403, 429) or ("EGW00133" in msg) or ("1분당" in msg)
                    if is_rate and attempt < 5:
                        sleep_s = min(120.0, delay + random.uniform(0.0, 1.0))
                        self.logger.warning("token rate-limited (%s) attempt=%s backoff=%.1fs", resp.status_code, attempt, sleep_s)
                        time.sleep(sleep_s)
                        delay = min(delay * 2.0, 60.0)
                        continue
                    resp.raise_for_status()

                data = resp.json()
                token = AuthToken(
                    access_token=str(data.get("access_token", "") or ""),
                    token_type=str(data.get("token_type", "Bearer") or "Bearer"),
                    expires_in=int(data.get("expires_in", 0) or 0),
                )
                if not token.access_token:
                    raise RuntimeError(f"token_invalid data={data}")

                self.token = token
                self._token_expire_at = self._now() + max(0, token.expires_in)
                self._save_token_cache()
                self.logger.info("auth token fetched")
                return token
            except Exception as e:
                last_err = e
                if attempt < 5:
                    sleep_s = min(120.0, delay + random.uniform(0.0, 1.0))
                    self.logger.warning("token fetch exception attempt=%s backoff=%.1fs: %s", attempt, sleep_s, e)
                    time.sleep(sleep_s)
                    delay = min(delay * 2.0, 60.0)
                    continue
                break

        raise RuntimeError(f"token_fetch_failed: {last_err}")

    def fetch_approval_key(self, force: bool = False) -> ApprovalKey:
        if (not force) and self.approval and self._now() < (self._approval_expire_at - 60):
            return self.approval

        self._wait_rate_limit("approval")

        url = f"{self.base_url}/oauth2/Approval"
        payload = {"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret}

        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(1, 6):
            try:
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code != 200:
                    msg = resp.text
                    is_rate = resp.status_code in (403, 429) or ("EGW00133" in msg) or ("1분당" in msg)
                    if is_rate and attempt < 5:
                        sleep_s = min(120.0, delay + random.uniform(0.0, 1.0))
                        self.logger.warning("approval rate-limited (%s) attempt=%s backoff=%.1fs", resp.status_code, attempt, sleep_s)
                        time.sleep(sleep_s)
                        delay = min(delay * 2.0, 60.0)
                        continue
                    resp.raise_for_status()

                data = resp.json()
                ap = ApprovalKey(
                    approval_key=str(data.get("approval_key", "") or ""),
                    expires_in=int(data.get("expires_in", 0) or 0),
                )
                if not ap.approval_key:
                    raise RuntimeError(f"approval_invalid data={data}")

                self.approval = ap
                self._approval_expire_at = self._now() + max(0, ap.expires_in)
                self.logger.info("websocket approval key fetched")
                return ap
            except Exception as e:
                last_err = e
                if attempt < 5:
                    sleep_s = min(120.0, delay + random.uniform(0.0, 1.0))
                    time.sleep(sleep_s)
                    delay = min(delay * 2.0, 60.0)
                    continue
                break

        raise RuntimeError(f"approval_fetch_failed: {last_err}")

    def hashkey(self, payload: dict) -> str:
        """POST body에 대한 KIS hashkey.

        일부 주문 엔드포인트는 header에 hashkey가 없으면 거부됩니다.
        """
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        h = str(data.get("HASH") or "")
        if not h:
            raise RuntimeError(f"hashkey_missing resp={data}")
        return h

    def auth_headers(self) -> dict:
        tok = self.fetch_token(force=False)
        return {
            "authorization": f"{tok.token_type} {tok.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
