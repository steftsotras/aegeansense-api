"""
FINoT API client.

Handles JWT authentication (with token caching) and batch historical-data
fetching using date ranges to stay under the API's 10k-record-per-request limit.
"""
import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class FinotClientError(Exception):
    """Base exception for FINoT client errors"""
    pass


class FinotAuthError(FinotClientError):
    """Authentication failed"""
    pass


class FinotClient:
    """Client for the FINoT API with token caching and batch data fetching.

    Instantiated per FinotProfile - there is no shared/global client, since
    each profile is a distinct FINoT tenant with its own credentials.
    """

    def __init__(self, base_url: str = "", username: str = "", password: str = ""):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: Optional[str] = None
        self._token_expires: Optional[datetime] = None

    def is_configured(self) -> bool:
        """Check if FINoT client is properly configured with all required credentials."""
        return bool(self.base_url and self.username and self.password)

    async def _get_token(self) -> str:
        """Get or refresh JWT token."""
        # Check if we have a valid cached token
        if self._token and self._token_expires:
            if datetime.now(timezone.utc) < self._token_expires:
                return self._token

        logger.info("Fetching new FINoT token...")

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/auth/jwt/login",
                    json={
                        "loginId": self.username,
                        "password": self.password
                    },
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json"
                    },
                    timeout=30.0
                )

                if response.status_code != 200:
                    logger.error(f"Auth failed with status: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    raise FinotAuthError(f"Authentication failed: {response.status_code}")

                data = response.json()

                # Response format: {"status": 200, "result": {"token": "...", "tenantName": "...", "user": {...}}}
                if data.get("status") != 200:
                    logger.error(f"Auth response status not 200: {data}")
                    raise FinotAuthError(f"Authentication failed: status {data.get('status')}")

                result = data.get("result", {})
                token = result.get("token")

                if not token:
                    logger.error(f"No token in auth response: {data}")
                    raise FinotAuthError("Could not extract token from response")

                self._token = token
                # Cache for 23 hours (tokens usually valid 24h)
                self._token_expires = datetime.now(timezone.utc) + timedelta(hours=23)

                tenant = result.get("tenantName", "unknown")
                user_email = result.get("user", {}).get("email", "unknown")
                logger.info(f"Successfully obtained FINoT token for {user_email} @ {tenant}")

                return self._token

            except httpx.HTTPStatusError as e:
                logger.error(f"Auth HTTP error: {e.response.status_code}")
                raise FinotAuthError(f"Authentication failed: {e.response.status_code}")
            except Exception as e:
                logger.error(f"Auth error: {e}")
                raise FinotAuthError(str(e))

    def _clear_token(self):
        """Clear cached token (for retry on 401)."""
        self._token = None
        self._token_expires = None

    async def fetch_stations(self) -> list[dict]:
        """
        Fetch all stations from FINoT inventory.

        Returns:
            List of station objects from FINoT
        """
        token = await self._get_token()

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/inventory/v1/objects",
                headers={
                    "Authorization": f"JWT {token}",
                    "Accept": "application/json"
                },
                timeout=30.0
            )

            # Handle 401 with retry
            if response.status_code == 401:
                logger.warning("Token expired, refreshing...")
                self._clear_token()
                token = await self._get_token()

                response = await client.get(
                    f"{self.base_url}/inventory/v1/objects",
                    headers={
                        "Authorization": f"JWT {token}",
                        "Accept": "application/json"
                    },
                    timeout=30.0
                )

            response.raise_for_status()
            data = response.json()

            objects = data.get("result", {}).get("objects", [])
            logger.info(f"Fetched {len(objects)} stations from FINoT")
            return objects

    async def fetch_historical_data(
        self,
        entity_id: str,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        attrs: Optional[list[str]] = None,
        aggr_method: Optional[str] = None,
        aggr_period: Optional[str] = None,
        last_n: Optional[int] = None
    ) -> dict:
        """
        Fetch historical data for an entity with optional parameters.

        IMPORTANT: Always specify from_date and to_date to avoid hitting the 10k record limit!

        Args:
            entity_id: The FINoT entity ID
            from_date: Start date (inclusive) - REQUIRED to avoid 10k limit
            to_date: End date (inclusive) - REQUIRED to avoid 10k limit
            attrs: List of attribute names to include
            aggr_method: Aggregation method (count, sum, avg, min, max)
            aggr_period: Aggregation period (year, month, day, hour, minute, second)
            last_n: Maximum number of results

        Returns:
            Dict with 'index' (timestamps) and 'attributes' (data arrays)
        """
        token = await self._get_token()

        # Build query parameters
        params = {}
        if from_date:
            params["fromDate"] = from_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if to_date:
            params["toDate"] = to_date.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        if attrs:
            params["attrs"] = ",".join(attrs)
        if aggr_method:
            params["aggrMethod"] = aggr_method
        if aggr_period:
            params["aggrPeriod"] = aggr_period
        if last_n:
            params["lastN"] = str(last_n)

        logger.info(f"Fetching historical data for {entity_id} with params: {params}")

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/inventory/v1/objects/{entity_id}/data",
                headers={
                    "Authorization": f"JWT {token}",
                    "Accept": "application/json"
                },
                params=params if params else None,
                timeout=60.0
            )

            # Handle 401 with retry
            if response.status_code == 401:
                logger.warning("Token expired during data fetch, refreshing...")
                self._clear_token()
                token = await self._get_token()

                response = await client.get(
                    f"{self.base_url}/inventory/v1/objects/{entity_id}/data",
                    headers={
                        "Authorization": f"JWT {token}",
                        "Accept": "application/json"
                    },
                    params=params if params else None,
                    timeout=60.0
                )

            response.raise_for_status()
            data = response.json()

            result = data.get("result", {})

            # Log what we got
            index_count = len(result.get("index", []))
            attr_count = len(result.get("attributes", []))
            logger.info(f"Received {index_count} timestamps, {attr_count} attributes for {entity_id}")

            return result

    async def test_connection(self) -> dict:
        """Test the connection and return status info."""
        try:
            login_result = await self._get_token()

            # Try to list entities
            stations = await self.fetch_stations()

            return {
                "success": True,
                "authenticated": True,
                "token_preview": f"{self._token[:20]}..." if self._token else None,
                "stations_found": len(stations)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


def client_from_profile(profile) -> "FinotClient":
    """Build a FinotClient from a FinotProfile row (decrypting its stored password)."""
    from .crypto import decrypt_password

    return FinotClient(
        base_url=profile.finot_url,
        username=profile.finot_username,
        password=decrypt_password(profile.finot_password_encrypted)
    )
