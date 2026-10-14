"""SQLite memory region implementation.

Reference implementation of memory region using SQLite for persistence.
"""
import sqlite3
import json
import time
from dataclasses import asdict
from topology.attest import create_atom_id
from contextlib import contextmanager
from typing import Dict, Any, Iterable, Iterator, List, Optional, Tuple

from topology.types import Atom, RegionKind


class MemSQLite:
    """SQLite-based memory region for atom storage and retrieval.
    
    Implements the Region protocol for memory operations with SQLite backend.
    """
    
    key: str
    kind: RegionKind = "memory"
    
    def __init__(self, key: str, path: str, domain: str):
        """Initialize SQLite memory region.
        
        Args:
            key: Region key
            path: SQLite database path
            domain: Domain for atom filtering
        """
        self.key = key
        self.path = path
        self.domain = domain
        self.trust = 0.85
        self.cost_profile = {"latency": 4.0, "tokens": 0.0, "risk": 0.05, "trust": self.trust}
        self._is_memory = path == ":memory:"
        self._shared_connection: Optional[sqlite3.Connection] = None
        if self._is_memory:
            uri = f"file:{self.key}?mode=memory&cache=shared"
            self._shared_connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._init_database()

    def _init_database(self) -> None:
        """Initialize SQLite database with atoms table."""
        with self._connect() as conn:
            # DDL and copy are one transaction: failed migrations preserve old rows.
            conn.execute("BEGIN IMMEDIATE")
            try:
                columns = conn.execute("PRAGMA table_info(atoms)").fetchall()
                key = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
                migrate = bool(columns) and key != ["domain", "id"]
                preserved_sql = []
                if migrate:
                    preserved_sql = [row[0] for row in conn.execute(
                        "SELECT sql FROM sqlite_master WHERE tbl_name='atoms' AND type IN ('index','trigger') AND sql IS NOT NULL")]
                    if key != ["id"] or {row[1] for row in columns} != {
                        "id", "modality", "content", "schema", "facets", "provenance", "policy", "created_at", "domain"
                    }:
                        raise ValueError("Unsupported atoms schema; refusing destructive migration")
                    conn.execute("ALTER TABLE atoms RENAME TO atoms_legacy_key")
                conn.execute("""CREATE TABLE IF NOT EXISTS atoms (
                    id TEXT NOT NULL, modality TEXT NOT NULL, content TEXT NOT NULL,
                    schema TEXT, facets TEXT NOT NULL, provenance TEXT NOT NULL,
                    policy TEXT NOT NULL, created_at INTEGER NOT NULL, domain TEXT NOT NULL,
                    PRIMARY KEY (domain, id))""")
                if migrate:
                    conn.execute("""INSERT INTO atoms (id,modality,content,schema,facets,provenance,policy,created_at,domain)
                        SELECT id,modality,content,schema,facets,provenance,policy,created_at,domain FROM atoms_legacy_key""")
                    conn.execute("DROP TABLE atoms_legacy_key")
                    for sql in preserved_sql:
                        conn.execute(sql)
                for column in ("domain", "modality", "created_at", "schema"):
                    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{column} ON atoms({column})")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._is_memory:
            assert self._shared_connection is not None
            yield self._shared_connection
        else:
            conn = sqlite3.connect(self.path)
            try:
                yield conn
            finally:
                conn.close()

    def read(self, query: Dict[str, Any]) -> Iterable[Atom]:
        """Read atoms matching query criteria.

        Args:
            query: Query parameters for filtering
            
        Yields:
            Atoms matching query criteria
        """
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Build WHERE clause from query
            where_conditions = []
            params = []
            
            # Domain filter
            if "domain" in query:
                where_conditions.append("domain = ?")
                params.append(query["domain"])
            elif "facets" in query and "domain" in query["facets"]:
                where_conditions.append("domain = ?")
                params.append(query["facets"]["domain"])
            else:
                where_conditions.append("domain = ?")
                params.append(self.domain)
            
            # Modality filter
            if "modality" in query:
                where_conditions.append("modality = ?")
                params.append(query["modality"])
            
            # Schema filter
            if "schema" in query:
                where_conditions.append("schema = ?")
                params.append(query["schema"])
            
            # Time range filter
            if "since" in query:
                where_conditions.append("created_at >= ?")
                params.append(query["since"])
            
            if "until" in query:
                where_conditions.append("created_at <= ?")
                params.append(query["until"])
            
            # json_each compares keys and decoded values exactly, without LIKE wildcards.
            facets = dict(query.get("facets", {}))
            for key in ("unit", "goal"):
                if key in query:
                    facets[key] = query[key]
            for key, value in facets.items():
                if key == "domain":
                    continue
                encoded = json.dumps(value, separators=(",", ":"), allow_nan=False)
                where_conditions.append("""EXISTS (SELECT 1 FROM json_each(atoms.facets) f
                    WHERE f.key = ? AND f.type = json_type(?) AND f.value IS json_extract(?, '$'))""")
                params.extend([key, encoded, encoded])

            # Build and execute query
            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
            order_clause = "ORDER BY created_at DESC, id ASC"
            limit_clause = "LIMIT ?"
            params.append(int(query.get("limit", 1000)))
            
            sql = f"""
                SELECT id, modality, content, schema, facets, provenance, policy, created_at
                FROM atoms 
                WHERE {where_clause}
                {order_clause}
                {limit_clause}
            """
            
            cursor.execute(sql, params)
            
            for row in cursor.fetchall():
                yield self._row_to_atom(row)
    
    def write(self, atoms: Iterable[Atom]) -> None:
        """Write atoms to storage.

        Args:
            atoms: Atoms to write
        """
        from core.trace import emit, validate
        recorded = []
        with self._connect() as conn:
            cursor = conn.cursor()

            for atom in atoms:
                # Prepare atom data
                facets = dict(atom.facets or {})
                created_ts = int(facets.get("ts", int(time.time() * 1000)))
                evidence = {"domain": self.domain, "atom": asdict(atom), "timestamp": created_ts}
                validate(evidence)
                recorded.append(evidence)
                atom_data = (
                    atom.id,
                    atom.modality,
                    json.dumps(atom.content),
                    atom.schema,
                    json.dumps(facets),
                    json.dumps(atom.provenance),
                    json.dumps(atom.policy),
                    created_ts,
                    self.domain
                )

                # Upsert atom
                cursor.execute("""
                    INSERT OR REPLACE INTO atoms 
                    (id, modality, content, schema, facets, provenance, policy, created_at, domain)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, atom_data)
            conn.commit()
        for evidence in recorded:
            emit("memory_atom", **evidence)

    def purge(self, domain: Optional[str] = None) -> None:
        """Remove stored atoms for ``domain`` (defaults to this region's domain)."""

        target = domain or self.domain
        with self._connect() as conn:
            conn.execute("DELETE FROM atoms WHERE domain = ?", (target,))
            conn.commit()
    
    def summarize(self, atoms: Iterable[Atom], goal: Dict[str, Any]) -> Atom:
        """Summarize atoms for goal context.
        
        Args:
            atoms: Atoms to summarize
            goal: Goal context
            
        Returns:
            Summary atom
        """
        atom_list = sorted(atoms, key=lambda atom: atom.id)
        
        if not atom_list:
            return self._create_empty_summary(goal)
        
        # Group atoms by schema for summarization
        schema_groups = {}
        for atom in atom_list:
            schema = atom.schema or "unknown"
            if schema not in schema_groups:
                schema_groups[schema] = []
            schema_groups[schema].append(atom)
        
        # Create summary content
        summary_content = {
            "total_atoms": len(atom_list),
            "schema_counts": {schema: len(atoms) for schema, atoms in schema_groups.items()},
            "domains": sorted(set(atom.facets.get("domain", "unknown") for atom in atom_list)),
            "modalities": sorted(set(atom.modality for atom in atom_list)),
            "time_range": {
                "earliest": min(atom.facets.get("ts", 0) for atom in atom_list),
                "latest": max(atom.facets.get("ts", 0) for atom in atom_list)
            },
            "goal": goal
        }
        
        # Create summary atom
        summary_atom = Atom(
            id=create_atom_id({"summary": summary_content, "sources": [asdict(a) for a in atom_list]},
                              "memory.summary@v1", {"domain": self.domain, "source": self.key}),
            modality="json",
            content=summary_content,
            schema="memory.summary@v1",
            facets={
                "domain": self.domain,
                "source": self.key,
                "ts": max((a.facets.get("ts", 0) for a in atom_list), default=0),
                "trust": 0.8,
                "summary_type": "dense_reducer"
            },
            provenance={
                "parents": [atom.id for atom in atom_list],
                "cost": {"tokens_in": 0, "tokens_out": 100, "ms": 50}
            },
            policy={"pii": False, "acl": []}
        )
        
        return summary_atom
    
    def reconcile(self, left: Atom, right: Atom, goal: Dict[str, Any]) -> Tuple[bool, Optional[Atom], Optional[str]]:
        """Reconcile two atoms, preferring newer or higher trust.
        
        Args:
            left: First atom
            right: Second atom
            goal: Goal context
            
        Returns:
            Tuple of (success, reconciled_atom, reason)
        """
        # Check if atoms are compatible for reconciliation
        if not self._atoms_compatible(left, right):
            return False, None, "incompatible_atoms"
        
        # Apply reconciliation strategy
        if self._should_prefer_newer(left, right):
            reconciled = right
            reason = "newer_wins"
        elif self._should_prefer_higher_trust(left, right):
            reconciled = right if right.facets.get("trust", 0.5) > left.facets.get("trust", 0.5) else left
            reason = "higher_trust_wins"
        else:
            reconciled = left
            reason = "first_wins"
        
        # Update provenance to reflect reconciliation
        updated_provenance = {
            **reconciled.provenance,
            "reconciled_with": [left.id, right.id],
            "reconciliation_reason": reason,
            "reconciled_at": int(time.time() * 1000)
        }
        
        reconciled_atom = Atom(
            id=reconciled.id,
            modality=reconciled.modality,
            content=reconciled.content,
            schema=reconciled.schema,
            facets=reconciled.facets,
            provenance=updated_provenance,
            policy=reconciled.policy
        )
        
        return True, reconciled_atom, reason
    
    def _row_to_atom(self, row: sqlite3.Row) -> Atom:
        """Convert database row to Atom.
        
        Args:
            row: Database row
            
        Returns:
            Atom instance
        """
        return Atom(
            id=row["id"],
            modality=row["modality"],
            content=json.loads(row["content"]),
            schema=row["schema"],
            facets=json.loads(row["facets"]),
            provenance=json.loads(row["provenance"]),
            policy=json.loads(row["policy"])
        )
    
    def _create_empty_summary(self, goal: Dict[str, Any]) -> Atom:
        """Create empty summary when no atoms available.
        
        Args:
            goal: Goal context
            
        Returns:
            Empty summary atom
        """
        return Atom(
            id=create_atom_id({"total_atoms": 0, "goal": goal}, "memory.summary@v1",
                              {"domain": self.domain, "source": self.key}),
            modality="json",
            content={"total_atoms": 0, "goal": goal},
            schema="memory.summary@v1",
            facets={
                "domain": self.domain,
                "source": self.key,
                "ts": 0,
                "trust": 0.5,
                "summary_type": "empty"
            },
            provenance={"parents": [], "cost": {"tokens_in": 0, "tokens_out": 10, "ms": 5}},
            policy={"pii": False, "acl": []}
        )
    
    def _atoms_compatible(self, left: Atom, right: Atom) -> bool:
        """Check if two atoms are compatible for reconciliation.
        
        Args:
            left: First atom
            right: Second atom
            
        Returns:
            True if atoms are compatible
        """
        return (
            left.schema == right.schema and
            left.modality == right.modality and
            left.facets.get("domain") == right.facets.get("domain")
        )
    
    def _should_prefer_newer(self, left: Atom, right: Atom) -> bool:
        """Check if newer atom should be preferred.
        
        Args:
            left: First atom
            right: Second atom
            
        Returns:
            True if newer should be preferred
        """
        left_ts = left.facets.get("ts", 0)
        right_ts = right.facets.get("ts", 0)
        
        return right_ts > left_ts
    
    def _should_prefer_higher_trust(self, left: Atom, right: Atom) -> bool:
        """Check if higher trust atom should be preferred.
        
        Args:
            left: First atom
            right: Second atom
            
        Returns:
            True if higher trust should be preferred
        """
        left_trust = left.facets.get("trust", 0.5)
        right_trust = right.facets.get("trust", 0.5)
        
        return abs(right_trust - left_trust) > 0.2  # Significant trust difference
