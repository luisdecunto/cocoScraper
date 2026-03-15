"""
Step 1 debug script — verify Nini full login chain.
Checkpoints:
  1. ValidateUser 200, .ASPXAUTH in cookies, Zone parsed
  2. getUnique returns sellerId and userName
  3. Active order ID printed (not None)
"""

import asyncio
import httpx
import json
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

BASE = "http://ecommerce.nini.com.ar:8081"


async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        username = os.getenv("NINI_USER")
        password = os.getenv("NINI_PASS")

        if not username or not password:
            print("ERROR: NINI_USER and/or NINI_PASS not set in .env")
            return

        print(f"Logging in as: {username}")

        # Step 1 — ValidateUser
        ts = int(time.time() * 1000)
        r = await client.get(
            f"{BASE}/ventas.administracion/Account/ValidateUser",
            params={
                "userName": username,
                "password": password,
                "callback": "_jqjsp",
                f"_{ts}": "",
            },
        )
        print(f"\n[Step 1] ValidateUser status: {r.status_code}")
        print(f"[Step 1] Response text: {r.text[:200]}")
        print(f"[Step 1] Has .ASPXAUTH: {'.ASPXAUTH' in client.cookies}")

        match = re.search(r'\((.+)\)', r.text)
        if not match:
            print("ERROR: could not parse JSONP response")
            return
        data = json.loads(match.group(1))
        zone = str(data["Zone"])
        print(f"[Step 1] Rol: {data.get('Rol')}  Zone: {zone}")

        # Step 2 — getUnique
        r2 = await client.post(
            f"{BASE}/nodejs/onlineUserDao/getUnique",
            data={
                "daoName":  "onlineUserDao",
                "method":   "getUnique",
                "params[]": username,
            },
        )
        print(f"\n[Step 2] getUnique status: {r2.status_code}")
        print(f"[Step 2] Raw response: {r2.text[:300]}")
        user = r2.json()[0]
        print(f"[Step 2] sellerId: {user['sellerId']}  userName: {user['userName']}")

        # Step 3 — findByClientId → active order
        r3 = await client.post(
            f"{BASE}/nodejs/onlineOrderDao/findByClientId",
            data={
                "daoName":               "onlineOrderDao",
                "method":                "findByClientId",
                "params[clientId]":      username,
                "params[sellerId]":      username,
                "params[isClient]":      "true",
                "params[userName]":      username,
                "params[zone]":          zone,
                "params[quotaSellerId]": username,
            },
        )
        print(f"\n[Step 3] findByClientId status: {r3.status_code}")
        orders = r3.json()
        print(f"[Step 3] Total orders in history: {len(orders)}")

        active = next((o for o in orders if o.get("orderEndDate") is None), None)
        if active:
            print(f"[Step 3] Active order id: {active['id']}")
            print(f"[Step 3] Active order keys: {list(active.keys())}")
        else:
            print("[Step 3] WARNING: no active order found (orderEndDate is None in all orders)")
            if orders:
                print(f"[Step 3] First order sample: {list(orders[0].keys())}")
                print(f"[Step 3] First order orderEndDate: {orders[0].get('orderEndDate')}")

        print("\n=== Summary ===")
        print(f"  .ASPXAUTH cookie set : {'.ASPXAUTH' in client.cookies}")
        print(f"  Zone                 : {zone}")
        print(f"  sellerId             : {user['sellerId']}")
        print(f"  userName             : {user['userName']}")
        print(f"  Active orderId       : {active['id'] if active else 'NOT FOUND'}")


asyncio.run(main())
