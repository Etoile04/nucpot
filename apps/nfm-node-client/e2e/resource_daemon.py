"""Small real resource-node process used by docker-compose.e2e.yml."""

from __future__ import annotations

import asyncio
import os
import uuid

from nfm_node_client import Credentials, NfmNodeClient, NodeType


async def main() -> None:
    hub_url = os.environ.get("HUB_URL", "http://hub:8000")
    node_id = os.environ["NODE_ID"]
    node_name = os.environ["NODE_NAME"]
    node_type = NodeType(os.environ.get("NODE_TYPE", "computing"))
    hub_node_id = uuid.UUID(os.environ["HUB_NODE_ID"])
    client = NfmNodeClient(
        hub_url=hub_url,
        credentials=Credentials(token=os.environ.get("HUB_TOKEN", "e2e")),
        heartbeat_interval=5.0,
        max_retries=2,
        backoff_base=0.1,
        backoff_max=1.0,
    )
    try:
        await client.register(
            name=node_name,
            node_type=node_type,
            api_endpoint=f"http://{node_name}:8080",
            hub_node_id=hub_node_id,
        )
        await client.heartbeat()
        await client.start_heartbeat_loop()
        while True:
            await asyncio.sleep(30)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
