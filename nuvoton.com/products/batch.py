import json
import os.path
from jsonschema import validate
import argparse
import subprocess

STRUCTURE_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "title": "Hierarchical Topics Schema",
    "type": "array",
    "minItems": 1,
    "items": {"$ref": "#/definitions/topic"},
    "definitions": {
        "topic": {
            "type": "object",
            "required": ["name", "sub_topics", "url"],
            "properties": {
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The name of the topic",
                },
                "sub_topics": {
                    "type": "array",
                    "items": {"$ref": "#/definitions/topic"},
                    "description": "Array of nested sub-topics",
                },
                "url": {
                    "type": "string",
                    "format": "uri",
                    "minLength": 1,
                    "description": "URL associated with the topic",
                },
                "breadcrumbs": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "description": "Breadcrumb navigation path (optional)",
                },
            },
            "additionalProperties": False,
        }
    },
}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--structure_file", type=str, required=True)
    parser.add_argument("--topic_range", type=str, default="*")
    parser.add_argument("--group_index", type=int, required=True)
    parser.add_argument("--group_count", type=int, required=True)
    parser.add_argument("--output_dir", type=str, required=True)
    args = parser.parse_args()

    with open(args.structure_file, "r") as f:
        data = json.load(f)

    validate(instance=data, schema=STRUCTURE_SCHEMA)

    def get_all_topics(topic):
        return [
            {
                "url": topic["url"],
                "path": "/".join([t.replace("/", "_slash_") for t in topic["breadcrumbs"]]),
            }
        ] + [
            descendant
            for sub_topic in topic["sub_topics"]
            for descendant in get_all_topics(sub_topic)
        ]

    topics = [descendant for topic in data for descendant in get_all_topics(topic)]
    topics = sorted(topics, key=lambda x: x["path"])

    if args.topic_range != "*":
        start, end = map(int, args.topic_range.split("-"))
        print(f"Filtering topics from {start} to {end}")

    print(f"Found {len(topics)} topics")

    batch_size = len(topics) // args.group_count + (len(topics) % args.group_count > 0)
    current_group = topics[
        args.group_index * batch_size : (args.group_index + 1) * batch_size
    ]

    for topic in current_group:
        print(f"Processing topic {topic['path']}")
        subprocess.run(
            [
                "python",
                "/app/crawl.py",
                "--url",
                topic["url"],
                "--out",
                os.path.join(args.output_dir, topic["path"]),
            ],
            check=True,
        )

if __name__ == "__main__":
    main()
