import asyncio

from stats import StatsStore


def test_stats_summary() -> None:
    async def scenario() -> None:
        store = StatsStore()
        await store.touch_user(100)
        await store.record(100, "pinterest", True, 1)
        await store.record(200, "instagram", True, 13)
        await store.record(200, "instagram", False, 0)
        summary = await store.summary()
        assert summary.total_users == 2
        assert summary.active_24h == 2
        assert summary.total_requests == 3
        assert summary.successful_requests == 2
        assert summary.media_files == 14
        assert summary.platform_successes == {"instagram": 1, "pinterest": 1}
        assert round(summary.success_rate, 1) == 66.7

    asyncio.run(scenario())
