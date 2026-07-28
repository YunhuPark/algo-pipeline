import os
import re
from typing import Callable, Any, Optional

class PreflightError(Exception):
    def __init__(self, code: str, message: str, meta_subcode: Optional[int] = None):
        self.code = code
        self.message = message
        self.meta_subcode = meta_subcode
        super().__init__(f"[{code}] {message}")

def redact_sensitive(text: str) -> str:
    if not text:
        return text
    # Mask IG access tokens (which are long alphanumeric strings)
    redacted = re.sub(r'EAA[a-zA-Z0-9]+', 'EAA***(redacted)', text)
    return redacted

class IGPreflightCheck:
    def __init__(self, http_client: Callable[[str, dict], dict] = None):
        # http_client should be a function that takes (url, params) and returns dict parsed from JSON
        self.http_client = http_client
        
    def check_token(self, token: str) -> dict[str, Any]:
        if not token:
            raise PreflightError("TOKEN_MISSING", "Instagram access token is not configured.")
            
        if not self.http_client:
            # If no client provided, we can't do network checks, but we assume valid in mock/dry-run unless injected
            return {"status": "mocked_ok"}
            
        # Meta Graph API Debug Token Endpoint
        url = "https://graph.facebook.com/debug_token"
        params = {"input_token": token, "access_token": token}
        
        try:
            response_data = self.http_client(url, params)
            return self._parse_response(response_data)
        except PreflightError:
            raise
        except Exception as e:
            raise PreflightError("NETWORK_ERROR", redact_sensitive(str(e)))
            
    def _parse_response(self, data: dict) -> dict[str, Any]:
        if "error" in data:
            err = data["error"]
            err_msg = redact_sensitive(err.get("message", "Unknown error"))
            code = err.get("code")
            subcode = err.get("error_subcode")
            
            # Meta Graph API Error Classification
            if code == 190:
                if subcode == 460:
                    raise PreflightError("TOKEN_EXPIRED", err_msg, subcode)
                else:
                    raise PreflightError("TOKEN_INVALID", err_msg, subcode)
            elif code in (10, 200, 210):
                raise PreflightError("PERMISSION_DENIED", err_msg, subcode)
            else:
                raise PreflightError("API_ERROR", err_msg, subcode)
                
        if "data" in data and "error" in data["data"]:
            # Sometimes embedded in data
            err = data["data"]["error"]
            err_msg = redact_sensitive(err.get("message", "Unknown error"))
            subcode = err.get("subcode") or err.get("error_subcode")
            code = err.get("code")
            if code == 190:
                raise PreflightError("TOKEN_INVALID", err_msg, subcode)
                
        return {"status": "ok", "app_id": data.get("data", {}).get("app_id")}
