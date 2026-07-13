"""Plan 65 Phase 4 — MessageBus tests (pub/sub, history, ordering, close)."""

from __future__ import annotations

import asyncio
import unittest

from duckln.harness.bus import Message, MessageBus


class MessageBusBasicTests(unittest.TestCase):
    def test_publish_returns_message_with_topic_and_sender(self) -> None:
        bus = MessageBus()
        msg = bus.publish("topic.x", {"k": "v"}, sender="alice")
        self.assertEqual(msg.topic, "topic.x")
        self.assertEqual(msg.sender, "alice")
        self.assertEqual(msg.payload, {"k": "v"})
        self.assertGreater(msg.timestamp, 0)

    def test_history_returns_published_messages_in_order(self) -> None:
        bus = MessageBus()
        bus.publish("t", "a", sender="s1")
        bus.publish("t", "b", sender="s2")
        bus.publish("t", "c", sender="s3")
        history = bus.history("t")
        self.assertEqual([m.payload for m in history], ["a", "b", "c"])

    def test_history_limit_caps_returned(self) -> None:
        bus = MessageBus()
        for i in range(10):
            bus.publish("t", i, sender="s")
        self.assertEqual(len(bus.history("t", limit=3)), 3)
        self.assertEqual([m.payload for m in bus.history("t", limit=3)], [7, 8, 9])

    def test_history_for_unknown_topic_empty(self) -> None:
        bus = MessageBus()
        self.assertEqual(bus.history("never_published"), ())

    def test_max_history_bound_prevents_unbounded_growth(self) -> None:
        bus = MessageBus(max_history_per_topic=3)
        for i in range(10):
            bus.publish("t", i, sender="s")
        history = bus.history("t", limit=100)
        self.assertEqual(len(history), 3)
        self.assertEqual([m.payload for m in history], [7, 8, 9])

    def test_topics_lists_all_published(self) -> None:
        bus = MessageBus()
        bus.publish("a", 1, sender="s")
        bus.publish("b", 2, sender="s")
        bus.publish("c", 3, sender="s")
        self.assertEqual(bus.topics(), ("a", "b", "c"))

    def test_publish_after_close_raises(self) -> None:
        bus = MessageBus()
        bus.close()
        with self.assertRaises(RuntimeError):
            bus.publish("t", 1, sender="s")


class MessageBusSubscribeTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscriber_receives_replayed_history(self) -> None:
        bus = MessageBus()
        bus.publish("t", "x", sender="s")
        bus.publish("t", "y", sender="s")
        received = []
        gen = bus.subscribe("t", replay_history=True)
        async def _consume():
            async for msg in gen:
                received.append(msg.payload)
                if len(received) >= 2:
                    return
        task = asyncio.create_task(_consume())
        await asyncio.wait_for(task, timeout=1.0)
        self.assertEqual(received, ["x", "y"])

    async def test_subscriber_receives_live_messages(self) -> None:
        bus = MessageBus()
        received = []
        gen = bus.subscribe("t", replay_history=True)
        async def _consume():
            async for msg in gen:
                received.append(msg.payload)
                if len(received) >= 2:
                    return
        consumer = asyncio.create_task(_consume())
        # Give the subscriber a chance to register.
        await asyncio.sleep(0.05)
        bus.publish("t", "live1", sender="s")
        bus.publish("t", "live2", sender="s")
        await asyncio.wait_for(consumer, timeout=2.0)
        self.assertEqual(received, ["live1", "live2"])

    async def test_close_unblocks_subscribers(self) -> None:
        bus = MessageBus()
        received = []
        gen = bus.subscribe("t", replay_history=False)
        async def _consume():
            async for msg in gen:
                received.append(msg.payload)
        consumer = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        bus.publish("t", "first", sender="s")
        await asyncio.sleep(0.05)
        bus.close()
        await asyncio.wait_for(consumer, timeout=2.0)
        self.assertEqual(received, ["first"])

    async def test_multiple_subscribers_each_get_a_copy(self) -> None:
        bus = MessageBus()
        received_a: list = []
        received_b: list = []
        gen_a = bus.subscribe("t", replay_history=False)
        gen_b = bus.subscribe("t", replay_history=False)

        async def _consume(gen, sink):
            async for msg in gen:
                sink.append(msg.payload)
                if len(sink) >= 2:
                    return

        task_a = asyncio.create_task(_consume(gen_a, received_a))
        task_b = asyncio.create_task(_consume(gen_b, received_b))
        await asyncio.sleep(0.05)
        bus.publish("t", "shared1", sender="s")
        bus.publish("t", "shared2", sender="s")
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=3.0)
        self.assertEqual(received_a, ["shared1", "shared2"])
        self.assertEqual(received_b, ["shared1", "shared2"])

    async def test_replay_false_skips_history(self) -> None:
        bus = MessageBus()
        bus.publish("t", "old1", sender="s")
        bus.publish("t", "old2", sender="s")
        received = []
        gen = bus.subscribe("t", replay_history=False)
        async def _consume():
            async for msg in gen:
                received.append(msg.payload)
                if len(received) >= 1:
                    return
        consumer = asyncio.create_task(_consume())
        await asyncio.sleep(0.05)
        bus.publish("t", "new1", sender="s")
        await asyncio.wait_for(consumer, timeout=2.0)
        self.assertEqual(received, ["new1"])


if __name__ == "__main__":
    unittest.main()
