"""Derived SQLite source graph, inspired by Teseo's build_graph and recall.

References from manuals are evidence, never runtime grants or load dependencies.
The attached database shares the registry publication transaction.
"""

import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict, deque

from .capabilities import _simple_frontmatter
from .contracts import Failure
from .packages import safe_path

VERSION = "teseo-source-graph-v1"
RELATIONS = ("requires", "preloads", "uses", "conflicts_with", "alias_of", "related_to")
STRONG = {"requires", "preloads", "uses", "alias_of"}


def normalize(value):
    value = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.findall(r"[a-z0-9]+", value))


def references(text):
    """Extract explicit names only; never follow a filesystem path or URL."""
    values = re.findall(r"\[\[([^\]]+)\]\]", text)
    values += re.findall(r"`([^`\n]+)`", text)
    values += re.findall(r"\*\*([^*\n]+)\*\*", text)
    values += re.findall(r"\[([^\]]+)\]\((?!https?://)[^)]+\)", text)
    if not values and re.fullmatch(r"[\w./:-]+", text.strip()):
        values = [text.strip()]
    return [v.split("|")[0].split("#")[0].strip() for v in values]


class SkillGraph:
    def __init__(self, db, state, check):
        self.db, self.check = db, check
        db.execute("ATTACH DATABASE ? AS skill_graph", (str(safe_path(state / "graph.sqlite3")),))
        if db.execute("PRAGMA skill_graph.user_version").fetchone()[0] not in {0, 1}:
            raise Failure("migration_required", "unknown_graph_schema")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS skill_graph.snapshots(
          catalog_id INTEGER PRIMARY KEY,signature TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS skill_graph.nodes(
          catalog_id INTEGER, id TEXT, name TEXT, kind TEXT, source_revision TEXT,
          PRIMARY KEY(catalog_id,id));
        CREATE TABLE IF NOT EXISTS skill_graph.aliases(
          catalog_id INTEGER, alias TEXT, id TEXT,
          PRIMARY KEY(catalog_id,alias,id),
          FOREIGN KEY(catalog_id,id) REFERENCES nodes(catalog_id,id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS skill_graph.edges(
          catalog_id INTEGER, source TEXT, target TEXT, relation TEXT,
          file TEXT, line INTEGER, evidence TEXT, authority TEXT,
          PRIMARY KEY(catalog_id,source,target,relation,file,line),
          FOREIGN KEY(catalog_id,source) REFERENCES nodes(catalog_id,id) ON DELETE CASCADE,
          FOREIGN KEY(catalog_id,target) REFERENCES nodes(catalog_id,id) ON DELETE CASCADE);
        CREATE INDEX IF NOT EXISTS skill_graph.edge_target ON edges(catalog_id,target);
        CREATE TABLE IF NOT EXISTS skill_graph.findings(
          catalog_id INTEGER, source TEXT, reference TEXT, reason TEXT, file TEXT,line INTEGER);
        PRAGMA skill_graph.user_version=1;
        """)

    def signature(self, catalog):
        row = self.db.execute(
            "SELECT signature FROM skill_graph.snapshots WHERE catalog_id=?", (catalog,)
        ).fetchone()
        return row[0] if row else None

    def build(self, catalog, files, *, batch_size=None):
        if batch_size is not None:
            return self._build_bounded(catalog, files, batch_size)
        rows = self.db.execute(
            "SELECT e.*,d.metadata FROM entries e JOIN descriptor_revisions d "
            "ON d.id=e.descriptor_revision WHERE catalog_id=? ORDER BY capability_id",
            (catalog,),
        ).fetchall()
        for table in ("snapshots", "aliases", "edges", "findings", "nodes"):
            self.db.execute(f"DELETE FROM skill_graph.{table} WHERE catalog_id=?", (catalog,))
        aliases, documents = defaultdict(set), []
        for row in rows:
            self.check()
            manifest, metadata = json.loads(row["manifest"]), json.loads(row["metadata"])
            identity = row["capability_id"]
            self.db.execute(
                "INSERT INTO skill_graph.nodes VALUES(?,?,?,?,?)",
                (catalog, identity, manifest["name"], manifest["kind"], row["source_revision"]),
            )
            package = files(identity, row["source_revision"], True)
            # Entry file is authoritative in source_revisions, including non-skill packages.
            entry = self.db.execute(
                "SELECT entry_file FROM source_revisions WHERE capability_id=? AND revision=?",
                (identity, row["source_revision"]),
            ).fetchone()[0]
            raw = package[entry].decode("utf-8")
            front = _simple_frontmatter(raw)[0] if raw.startswith("---") else {}
            extra = front.get("aliases", [])
            extra = extra if isinstance(extra, list) else [extra]
            names = [
                identity,
                manifest["name"],
                identity.rsplit("/", 1)[-1],
                *metadata.get("aliases", []),
                *extra[:32],
            ]
            for name in names:
                if isinstance(name, str) and normalize(name):
                    aliases[normalize(name)].add(identity)
            documents.append((identity, manifest, entry, raw, front))
        self.db.executemany(
            "INSERT INTO skill_graph.aliases VALUES(?,?,?)",
            ((catalog, a, i) for a, ids in aliases.items() for i in sorted(ids)),
        )

        self._link_documents(catalog, documents, aliases)
        payload = [VERSION, [tuple(r) for r in rows]]
        signature = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.db.execute("INSERT INTO skill_graph.snapshots VALUES(?,?)", (catalog, signature))
        return True

    def _build_bounded(self, catalog, files, batch_size):
        """Build in an invisible shadow catalog, then publish atomically.

        No raw manuals are persisted in the stage. Each bounded request validates
        its source snapshots again; a failed request rolls back its cursor and rows.
        """
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise Failure("policy_denied", "invalid_graph_batch")
        rows = self.db.execute(
            "SELECT e.*,d.metadata FROM entries e JOIN descriptor_revisions d "
            "ON d.id=e.descriptor_revision WHERE catalog_id=? ORDER BY capability_id",
            (catalog,),
        ).fetchall()
        signature = hashlib.sha256(
            json.dumps([VERSION, [tuple(row) for row in rows]], sort_keys=True).encode()
        ).hexdigest()
        key, shadow = f"graph_build:{catalog}", -catalog
        saved = self.db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        stage = json.loads(saved[0]) if saved else None
        tables = ("snapshots", "aliases", "edges", "findings", "nodes")
        if not stage or stage.get("signature") != signature:
            for table in tables:
                self.db.execute(f"DELETE FROM skill_graph.{table} WHERE catalog_id=?", (shadow,))
            stage = {"signature": signature, "phase": "nodes", "cursor": 0}
        remaining = batch_size
        while remaining and stage["phase"] != "complete":
            self.check()
            offset = stage["cursor"]
            chunk = rows[offset : offset + remaining]
            documents = []
            for row in chunk:
                self.check()
                identity = row["capability_id"]
                manifest = json.loads(row["manifest"])
                package = files(identity, row["source_revision"], True)
                entry = self.db.execute(
                    "SELECT entry_file FROM source_revisions WHERE capability_id=? AND revision=?",
                    (identity, row["source_revision"]),
                ).fetchone()[0]
                raw = package[entry].decode("utf-8")
                front = _simple_frontmatter(raw)[0] if raw.startswith("---") else {}
                documents.append((identity, manifest, entry, raw, front))
                if stage["phase"] == "nodes":
                    self.db.execute(
                        "INSERT INTO skill_graph.nodes VALUES(?,?,?,?,?)",
                        (
                            shadow,
                            identity,
                            manifest["name"],
                            manifest["kind"],
                            row["source_revision"],
                        ),
                    )
                    extra = front.get("aliases", [])
                    extra = extra if isinstance(extra, list) else [extra]
                    names = [
                        identity,
                        manifest["name"],
                        identity.rsplit("/", 1)[-1],
                        *json.loads(row["metadata"]).get("aliases", []),
                        *extra[:32],
                    ]
                    for name in names:
                        if isinstance(name, str) and normalize(name):
                            self.db.execute(
                                "INSERT OR IGNORE INTO skill_graph.aliases VALUES(?,?,?)",
                                (shadow, normalize(name), identity),
                            )
            if stage["phase"] == "edges":
                aliases = defaultdict(set)
                for row in self.db.execute(
                    "SELECT alias,id FROM skill_graph.aliases WHERE catalog_id=?", (shadow,)
                ):
                    aliases[row[0]].add(row[1])
                self._link_documents(shadow, documents, aliases)
            remaining -= len(chunk)
            stage["cursor"] += len(chunk)
            if stage["cursor"] == len(rows):
                stage["phase"] = "edges" if stage["phase"] == "nodes" else "complete"
                stage["cursor"] = 0
        if stage["phase"] != "complete":
            self.db.execute(
                "INSERT INTO config VALUES(?,?) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value",
                (key, json.dumps(stage)),
            )
            return False
        self.check()
        for table in tables:
            self.db.execute(f"DELETE FROM skill_graph.{table} WHERE catalog_id=?", (catalog,))
        columns = {
            "nodes": "id,name,kind,source_revision",
            "aliases": "alias,id",
            "edges": "source,target,relation,file,line,evidence,authority",
            "findings": "source,reference,reason,file,line",
        }
        for table, names in columns.items():
            self.check()
            self.db.execute(
                f"INSERT INTO skill_graph.{table} SELECT ?,{names} "
                f"FROM skill_graph.{table} WHERE catalog_id=?",
                (catalog, shadow),
            )
        for table in tables:
            self.db.execute(f"DELETE FROM skill_graph.{table} WHERE catalog_id=?", (shadow,))
        self.db.execute("INSERT INTO skill_graph.snapshots VALUES(?,?)", (catalog, signature))
        self.db.execute("DELETE FROM config WHERE key=?", (key,))
        return True

    def _link_documents(self, catalog, documents, aliases):
        def link(source, value, relation, file, line, evidence, authority):
            targets = aliases.get(normalize(value), set())
            if len(targets) != 1:
                self.db.execute(
                    "INSERT INTO skill_graph.findings VALUES(?,?,?,?,?,?)",
                    (
                        catalog,
                        source,
                        value[:256],
                        "ambiguous_reference" if targets else "unresolved_reference",
                        file,
                        line,
                    ),
                )
            else:
                target = next(iter(targets))
                if target != source:
                    self.db.execute(
                        "INSERT OR IGNORE INTO skill_graph.edges VALUES(?,?,?,?,?,?,?,?)",
                        (catalog, source, target, relation, file, line, evidence[:512], authority),
                    )

        for identity, manifest, entry, raw, front in documents:
            self.check()
            for relation in RELATIONS:
                values = manifest.get(relation, [])
                values = [values] if isinstance(values, str) else values
                for value in values:
                    link(identity, value, relation, entry, 1, value, "operator_manifest")
            lines = raw.splitlines()
            front_end = next((i for i in range(1, len(lines)) if lines[i] == "---"), -1)
            count = 0
            for key in (*RELATIONS, "related"):
                values = front.get(key, [])
                values = values if isinstance(values, list) else [values]
                line = next(
                    (i + 1 for i, v in enumerate(lines[:front_end]) if v.startswith(key + ":")), 1
                )
                for value in values[:32]:
                    if isinstance(value, str):
                        for ref in references(value):
                            link(
                                identity,
                                ref,
                                "related_to" if key == "related" else key,
                                entry,
                                line,
                                value,
                                "source_advisory",
                            )
                            count += 1
            fence, related_level = None, None
            for number, line in enumerate(lines, 1):
                if front_end >= 0 and number <= front_end + 1:
                    continue
                marker = re.match(r"\s*(`{3,}|~{3,})", line)
                if marker:
                    if fence is None:
                        fence = marker[1][0]
                    elif marker[1][0] == fence:
                        fence = None
                    continue
                if fence:
                    continue
                heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", line)
                if heading:
                    if related_level and len(heading[1]) <= related_level:
                        related_level = None
                    title = normalize(heading[2])
                    if title in {
                        "related",
                        "related skills",
                        "related components",
                        "relationship to other skills",
                    }:
                        related_level = len(heading[1])
                    continue
                if count >= 256:
                    self.db.execute(
                        "INSERT INTO skill_graph.findings VALUES(?,?,?,?,?,?)",
                        (catalog, identity, "", "reference_limit", entry, number),
                    )
                    break
                if related_level:
                    if re.match(r"\s*[-*]?\s*(?:\*\*)?(?:Agents?|Rules?|Commands?):", line, re.I):
                        continue
                    refs, relation = references(line), "related_to"
                else:
                    plain = re.sub(r"`+[^`]*`+", "", line)
                    refs = re.findall(r"\[\[([^\]]+)\]\]", plain)
                    refs = [r.split("|")[0].split("#")[0] for r in refs]
                    relation = "mentions"
                for ref in refs:
                    if count >= 256:
                        break
                    link(identity, ref, relation, entry, number, line, "source_advisory")
                    count += 1

    def inspect(self, catalog, identity=None, limit=20, offset=0):
        result = {
            "ready": self.signature(catalog) is not None,
            "signature": self.signature(catalog),
            "catalog_id": catalog,
            "schema_version": 1,
        }
        for table, key in (("nodes", "node"), ("edges", "edge"), ("findings", "finding")):
            result[key + "_count"] = self.db.execute(
                f"SELECT count(*) FROM skill_graph.{table} WHERE catalog_id=?", (catalog,)
            ).fetchone()[0]
            where, args = "catalog_id=?", [catalog]
            if identity:
                where += " AND " + ("id=?" if table == "nodes" else "source=?")
                args.append(identity)
            result[table] = [
                dict(r)
                for r in self.db.execute(
                    f"SELECT * FROM skill_graph.{table} WHERE {where} "
                    "ORDER BY rowid LIMIT ? OFFSET ?",
                    (*args, limit, offset),
                )
            ]
        return result

    def expand(self, catalog, query, lexical, eligible):
        """Bounded directed traversal; weak links apply only to the first hop."""
        if not self.signature(catalog):
            return lexical, {}
        tokens = normalize(query).split()[:64]
        seeds = []
        for start in range(len(tokens)):
            for size in range(min(8, len(tokens) - start), 0, -1):
                rows = self.db.execute(
                    "SELECT id FROM skill_graph.aliases WHERE catalog_id=? AND alias=?",
                    (catalog, " ".join(tokens[start : start + size])),
                ).fetchall()
                if len(rows) == 1 and eligible(rows[0][0]):
                    seeds.append(rows[0][0])
                    break
        seeds = list(dict.fromkeys(seeds))[:5]
        for row in lexical:
            if len(seeds) == 5:
                break
            identity = row["capability_id"]
            if identity not in seeds and eligible(identity):
                seeds.append(identity)
        scores = {r["capability_id"]: 100 / (n + 1) for n, r in enumerate(lexical)}
        traces = {}
        for seed in seeds:
            if seed not in scores:
                scores[seed] = 100
            for direction, maximum in (("source", 3), ("target", 2)):
                queue, seen = deque([(seed, [])]), {seed}
                while queue and len(seen) < 100:
                    self.check()
                    current, path = queue.popleft()
                    edges = self.db.execute(
                        f"SELECT * FROM skill_graph.edges WHERE catalog_id=? "
                        f"AND {direction}=? ORDER BY relation,target,source LIMIT 100",
                        (catalog, current),
                    ).fetchall()
                    for edge in edges:
                        relation = edge["relation"]
                        if relation == "conflicts_with" or (path and relation not in STRONG):
                            continue
                        target = edge["target"] if direction == "source" else edge["source"]
                        if target in seen or not eligible(target):
                            continue
                        seen.add(target)
                        trail = path + [
                            {
                                k: edge[k]
                                for k in (
                                    "source",
                                    "target",
                                    "relation",
                                    "file",
                                    "line",
                                    "authority",
                                )
                            }
                        ]
                        degree = self.db.execute(
                            "SELECT count(*) FROM skill_graph.edges WHERE "
                            "catalog_id=? AND (source=? OR target=?)",
                            (catalog, target, target),
                        ).fetchone()[0]
                        score = (45 if relation in STRONG else 10) / (
                            len(trail) * max(1, math.log2(1 + degree))
                        )
                        if direction == "target":
                            score *= 0.6
                        if target not in scores or score > scores[target]:
                            scores[target] = score
                            traces[target] = {"method": "graph", "seed": seed, "path": trail}
                        if len(trail) < maximum and len(seen) < 100:
                            queue.append((target, trail))
        ranked = [{"capability_id": i} for i in sorted(scores, key=lambda i: (-scores[i], i))]
        return ranked, traces
