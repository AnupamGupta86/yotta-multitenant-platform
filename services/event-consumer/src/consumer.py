import json
import logging
import os
import time

from kafka import KafkaConsumer, TopicPartition

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("website-event-consumer")


def main():
    servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    topic = os.environ.get("KAFKA_TOPIC", "website-events")
    while True:
        consumer = None
        try:
            consumer = KafkaConsumer(
                bootstrap_servers=servers.split(","),
                auto_offset_reset="earliest",
                enable_auto_commit=False,
                value_deserializer=lambda value: json.loads(value.decode("utf-8")),
            )
            partitions = consumer.partitions_for_topic(topic) or set()
            while not partitions:
                logger.info("Waiting for topic=%s to be created", topic)
                time.sleep(1)
                partitions = consumer.partitions_for_topic(topic) or set()
            topic_partitions = [TopicPartition(topic, partition) for partition in partitions]
            consumer.assign(topic_partitions)
            consumer.seek_to_beginning(*topic_partitions)
            logger.info("Consuming topic=%s brokers=%s", topic, servers)
            while True:
                for records in consumer.poll(timeout_ms=1000).values():
                    for record in records:
                        logger.info("Consumed event: %s", json.dumps(record.value, sort_keys=True))
        except Exception:
            logger.exception("Kafka consumer disconnected; retrying")
            time.sleep(3)
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    logger.exception("Error closing Kafka consumer")


if __name__ == "__main__":
    main()
