#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))

from modules.fetch_opinion import OpinionFetcher

fetcher = OpinionFetcher(
    private_key=os.getenv('OPINION_PRIVATE_KEY'),
    token=os.getenv('OPINION_TOKEN'),
)

markets = fetcher.fetch_markets(limit=10, fetch_all=False)
print(f"获取到 {len(markets)} 个市场")

for m in markets:
    if not m.get('isMulti'):
        title = m.get('title', '')[:40]
        print(f"\n市场: {title}")
        print(f"  topicId: {m.get('topicId')}")
        print(f"  marketId: {m.get('marketId')}")
        print(f"  questionId: {m.get('questionId')}")
        print(f"  tokenId: {m.get('tokenId')}")
        print(f"  yesTokenId: {m.get('yesTokenId')}")
        break
