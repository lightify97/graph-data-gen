"""Neo4j connection for the scan-structure validation instance."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from neo4j import Driver, GraphDatabase

DEFAULT_URI = os.getenv("SKYGENIC_NEO4J_URI", "bolt://localhost:7688")
DEFAULT_USER = os.getenv("SKYGENIC_NEO4J_USER", "neo4j")
DEFAULT_PASSWORD = os.getenv("SKYGENIC_NEO4J_PASSWORD", "skygenic-scans-dev")


def get_driver(uri: str = DEFAULT_URI, user: str = DEFAULT_USER,
               password: str = DEFAULT_PASSWORD) -> Driver:
    return GraphDatabase.driver(uri, auth=(user, password))


@contextmanager
def session(driver: Driver | None = None) -> Iterator[Any]:
    own = driver is None
    drv = driver or get_driver()
    try:
        with drv.session() as s:
            yield s
    finally:
        if own:
            drv.close()


def run(cypher: str, **params: Any) -> list[dict]:
    with session() as s:
        return [r.data() for r in s.run(cypher, **params)]
