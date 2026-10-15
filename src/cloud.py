"""Cloud backend abstraction for cardiac monitoring device.

Supports Firebase Realtime Database, Supabase PostgreSQL, or local mock.
Provides unified interface for batch send, result retrieval, and retry logic.
"""

import json
import time
import asyncio
from typing import Dict, Optional, List, Callable
from enum import Enum
from abc import ABC, abstractmethod


class CloudProvider(Enum):
    FIREBASE = "firebase"
    SUPABASE = "supabase"
    MOCK = "mock"  # for testing


class CloudBackend(ABC):
    """Abstract base for cloud backends."""

    @abstractmethod
    async def send_batch(self, batch: Dict) -> bool:
        """Send a batch of samples to cloud.

        Args:
            batch: dict with 'samples' list and metadata

        Returns:
            True if accepted by cloud, False otherwise
        """
        pass

    @abstractmethod
    async def get_results(self, batch_id: str) -> Optional[Dict]:
        """Retrieve processing results from cloud.

        Args:
            batch_id: identifier for the batch

        Returns:
            dict with results or None if not ready
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Quick health check of cloud connection.

        Returns:
            True if cloud is reachable, False otherwise
        """
        pass


class FirebaseBackend(CloudBackend):
    """Firebase Realtime Database backend (free tier: 100 concurrent connections, 1 GB storage).

    Stores samples under /mayacardio/batches/{batch_id}/samples
    Stores results under /mayacardio/results/{batch_id}
    """

    def __init__(self, project_id: str, api_key: str):
        self.project_id = project_id
        self.api_key = api_key
        self.base_url = f"https://{project_id}.firebaseio.com"
        self._session = None
        self._batch_counter = 0

    async def send_batch(self, batch: Dict) -> bool:
        """Send batch to Firebase Realtime Database."""
        try:
            import aiohttp

            if not self._session:
                self._session = aiohttp.ClientSession()

            batch_id = f"batch_{int(time.time() * 1000)}_{self._batch_counter}"
            self._batch_counter += 1

            url = f"{self.base_url}/mayacardio/batches/{batch_id}.json?auth={self.api_key}"
            async with self._session.put(url, json=batch, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return resp.status in (200, 201)
        except Exception:
            return False

    async def get_results(self, batch_id: str) -> Optional[Dict]:
        """Retrieve results from Firebase."""
        try:
            import aiohttp

            if not self._session:
                self._session = aiohttp.ClientSession()

            url = f"{self.base_url}/mayacardio/results/{batch_id}.json?auth={self.api_key}"
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception:
            return None

    async def health_check(self) -> bool:
        """Check Firebase connectivity."""
        try:
            import aiohttp

            if not self._session:
                self._session = aiohttp.ClientSession()

            url = f"{self.base_url}/.json?auth={self.api_key}"
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status in (200, 404)  # 404 is ok; means no data yet
        except Exception:
            return False


class SupabaseBackend(CloudBackend):
    """Supabase PostgreSQL backend (free tier: 500 MB, 2 GB bandwidth/month).

    Stores samples in 'samples' table:
        id SERIAL PRIMARY KEY
        batch_id TEXT
        ts BIGINT
        window_index INT
        s1_ch1 FLOAT, s1_ch2 FLOAT, s1_ch3 FLOAT
        s2_ch1 FLOAT, s2_ch2 FLOAT
        crc32 INT
        created_at TIMESTAMP

    Stores results in 'batch_results' table:
        id SERIAL PRIMARY KEY
        batch_id TEXT UNIQUE
        status TEXT (processing|completed|failed)
        result_data JSONB
        created_at TIMESTAMP
    """

    def __init__(self, project_url: str, api_key: str):
        self.project_url = project_url
        self.api_key = api_key
        self._session = None

    async def send_batch(self, batch: Dict) -> bool:
        """Send batch to Supabase PostgreSQL."""
        try:
            import aiohttp

            if not self._session:
                self._session = aiohttp.ClientSession()

            url = f"{self.project_url}/rest/v1/samples"
            headers = {
                "apikey": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            # Insert samples into Supabase
            batch_id = f"batch_{int(time.time() * 1000)}"
            rows = []
            for sample in batch.get("samples", []):
                rows.append({
                    "batch_id": batch_id,
                    "ts": sample["ts"],
                    "window_index": sample["window_index"],
                    "s1_ch1": sample["s1_ch1"],
                    "s1_ch2": sample["s1_ch2"],
                    "s1_ch3": sample["s1_ch3"],
                    "s2_ch1": sample["s2_ch1"],
                    "s2_ch2": sample["s2_ch2"],
                    "crc32": sample["crc32"],
                })

            async with self._session.post(
                url,
                json=rows,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return resp.status in (200, 201)
        except Exception:
            return False

    async def get_results(self, batch_id: str) -> Optional[Dict]:
        """Retrieve results from Supabase."""
        try:
            import aiohttp

            if not self._session:
                self._session = aiohttp.ClientSession()

            url = f"{self.project_url}/rest/v1/batch_results?batch_id=eq.{batch_id}"
            headers = {
                "apikey": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
            }

            async with self._session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data[0] if data else None
                return None
        except Exception:
            return None

    async def health_check(self) -> bool:
        """Check Supabase connectivity."""
        try:
            import aiohttp

            if not self._session:
                self._session = aiohttp.ClientSession()

            url = f"{self.project_url}/rest/v1/"
            headers = {
                "apikey": self.api_key,
                "Authorization": f"Bearer {self.api_key}",
            }

            async with self._session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                return resp.status in (200, 404)
        except Exception:
            return False


class MockBackend(CloudBackend):
    """Mock cloud backend for testing (stores in memory).

    Simulates latency and can inject failures for testing resilience.
    """

    def __init__(self, latency_ms: float = 100, fail_rate: float = 0.0):
        self.latency_ms = latency_ms
        self.fail_rate = fail_rate
        self._batches: Dict[str, Dict] = {}
        self._results: Dict[str, Dict] = {}
        self._batch_counter = 0

    async def send_batch(self, batch: Dict) -> bool:
        """Accept batch and simulate cloud processing."""
        import random

        await asyncio.sleep(self.latency_ms / 1000.0)

        if random.random() < self.fail_rate:
            return False

        batch_id = f"batch_{self._batch_counter}"
        self._batch_counter += 1

        self._batches[batch_id] = batch
        # Simulate results after short delay
        self._results[batch_id] = {
            "batch_id": batch_id,
            "status": "completed",
            "processed_count": len(batch.get("samples", [])),
            "timestamp": int(time.time() * 1000),
        }

        return True

    async def get_results(self, batch_id: str) -> Optional[Dict]:
        """Return simulated results."""
        await asyncio.sleep(self.latency_ms / 1000.0)
        return self._results.get(batch_id)

    async def health_check(self) -> bool:
        """Mock is always healthy."""
        return True


class CloudClient:
    """Unified cloud client with retry logic and batch management."""

    def __init__(
        self,
        provider: CloudProvider = CloudProvider.MOCK,
        project_id: str = "",
        api_key: str = "",
        max_retries: int = 3,
        retry_delay_ms: float = 1000.0,
    ):
        self.provider = provider
        self.max_retries = max_retries
        self.retry_delay_ms = retry_delay_ms

        if provider == CloudProvider.FIREBASE:
            self.backend: CloudBackend = FirebaseBackend(project_id, api_key)
        elif provider == CloudProvider.SUPABASE:
            self.backend = SupabaseBackend(project_id, api_key)
        else:
            self.backend = MockBackend()

        self._pending_batches: Dict[str, Dict] = {}

    async def send_batch_with_retry(self, batch: Dict) -> bool:
        """Send batch with exponential backoff retry."""
        for attempt in range(self.max_retries):
            try:
                success = await self.backend.send_batch(batch)
                if success:
                    batch_id = f"batch_{int(time.time() * 1000)}"
                    self._pending_batches[batch_id] = batch
                    return True
                else:
                    # Exponential backoff
                    delay = self.retry_delay_ms * (2 ** attempt) / 1000.0
                    await asyncio.sleep(delay)
            except Exception:
                # Continue to retry
                pass

        return False

    async def get_results_for_batch(self, batch_id: str) -> Optional[Dict]:
        """Get results for a batch if ready."""
        return await self.backend.get_results(batch_id)

    async def health_check(self) -> bool:
        """Check cloud connectivity."""
        return await self.backend.health_check()
