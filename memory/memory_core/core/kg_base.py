#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core facade: implement KGBase directly on Neo4j (no persistence layer)."""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .neo4j_store import Neo4jStore

if TYPE_CHECKING:
    from ..system.event_bus import EventBus

logger = logging.getLogger(__name__)

CoreResult = Dict[str, Any]


def _safe_json_dumps(value: Any, fallback: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return json.dumps(fallback, ensure_ascii=False)


def _safe_json_loads(value: Any, fallback: Any):
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


class KGBase:
    """Entity/relation operations implemented directly against Neo4j."""

    def __init__(
        self,
        workflow_id: str,
        event_bus: Optional["EventBus"] = None,
    ) -> None:
        self.workflow_id = str(workflow_id)
        self.event_bus = event_bus
        self.store = Neo4jStore.instance()
        self.database = self.store.resolve_database(self.workflow_id)
        self.store.ensure_database(self.database)
        logger.info(
            "KGBase initialized (neo4j=%s): workflow_id=%s database=%s",
            self.store.available,
            self.workflow_id,
            self.database or "<default>",
        )

    def _publish_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event_type, payload)

    def _disabled_result(self, operation: str) -> CoreResult:
        return {
            "success": False,
            "changed": False,
            "details": {"operation": operation, "error": "Neo4j is not available"},
        }

    def _run_read(self, query: str, params: Optional[Dict[str, Any]] = None):
        return self.store.run(
            query,
            params or {},
            write=False,
            database=self.database,
        )

    def _run_write(self, query: str, params: Optional[Dict[str, Any]] = None):
        return self.store.run(
            query,
            params or {},
            write=True,
            database=self.database,
        )

    def _entity_exists(self, entity_id: str) -> bool:
        if not entity_id or not self.store.available:
            return False
        rows = self._run_read(
            "MATCH (e:Entity {id: $entity_id}) RETURN count(e) AS c",
            {"entity_id": entity_id},
        )
        return bool(rows and int(rows[0]["c"]) > 0)

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------
    def add_entity(
        self,
        entity_id: str,
        entity_type: Optional[str] = None,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("add_entity")
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return {"success": False, "changed": False, "details": {"operation": "add_entity", "error": "empty id"}}
        if self._entity_exists(entity_id):
            return {
                "success": False,
                "changed": False,
                "details": {"operation": "add_entity", "error": f"entity already exists: {entity_id}"},
            }

        payload = {
            "id": entity_id,
            "uid": str(uuid.uuid4()),
            "name": entity_id,
            "type": str(entity_type or ""),
            "confidence": 1.0,
            "sources_json": _safe_json_dumps([source_info] if source_info else [], []),
            "features_json": _safe_json_dumps([], []),
            "attributes_json": _safe_json_dumps([], []),
            "metadata_json": _safe_json_dumps({}, {}),
        }
        query = """
        CREATE (e:Entity {
            id: $id, uid: $uid, name: $name, type: $type, confidence: $confidence,
            sources_json: $sources_json, features_json: $features_json,
            attributes_json: $attributes_json, metadata_json: $metadata_json
        })
        """
        try:
            self._run_write(query, payload)
            self._publish_event("ENTITY_ADDED", {"entity_id": entity_id})
            return {
                "success": True,
                "changed": True,
                "details": {"operation": "add_entity", "entity_id": entity_id, "entity_uid": payload["uid"]},
            }
        except Exception as exc:
            return {"success": False, "changed": False, "details": {"operation": "add_entity", "error": str(exc)}}

    def get_entity(self, entity_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        if not self.store.available:
            return False, None
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return False, None
        query = """
        MATCH (e:Entity {id: $entity_id})
        RETURN e.id AS id, e.uid AS uid, e.name AS name, e.type AS type,
               e.confidence AS confidence, e.sources_json AS sources_json,
               e.features_json AS features_json, e.attributes_json AS attributes_json,
               e.metadata_json AS metadata_json
        """
        try:
            rows = self._run_read(query, {"entity_id": entity_id})
            if not rows:
                return False, None
            row = rows[0]
            return True, {
                "id": str(row.get("id") or entity_id),
                "uid": str(row.get("uid") or ""),
                "name": str(row.get("name") or entity_id),
                "type": str(row.get("type") or ""),
                "confidence": float(row.get("confidence") or 1.0),
                "sources": _safe_json_loads(row.get("sources_json"), []),
                "features": _safe_json_loads(row.get("features_json"), []),
                "attributes": _safe_json_loads(row.get("attributes_json"), []),
                "metadata": _safe_json_loads(row.get("metadata_json"), {}),
            }
        except Exception:
            return False, None

    def merge_entities(
        self,
        target_id: str,
        source_id: str,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("merge_entities")
        target_id = str(target_id or "").strip()
        source_id = str(source_id or "").strip()
        if not target_id or not source_id or target_id == source_id:
            return {
                "success": False,
                "changed": False,
                "details": {"operation": "merge_entities", "error": "invalid target/source"},
            }
        ok_t, target = self.get_entity(target_id)
        ok_s, source = self.get_entity(source_id)
        if not ok_t or not ok_s or target is None or source is None:
            return {
                "success": False,
                "changed": False,
                "details": {"operation": "merge_entities", "error": "target/source entity not found"},
            }

        # Merge entity payload (keep target as canonical).
        target["sources"] = self._merge_sources(target.get("sources"), source.get("sources"))
        target["features"] = self._merge_features(target.get("features"), source.get("features"))
        target["attributes"] = self._merge_attributes(target.get("attributes"), source.get("attributes"))
        if source_info:
            target["sources"] = self._merge_sources(target.get("sources"), [source_info])
        if not target.get("type") and source.get("type"):
            target["type"] = source.get("type")

        update_query = """
        MATCH (e:Entity {id: $id})
        SET e.sources_json = $sources_json,
            e.features_json = $features_json,
            e.attributes_json = $attributes_json,
            e.metadata_json = $metadata_json,
            e.type = $type
        """
        try:
            self._run_write(
                update_query,
                {
                    "id": target_id,
                    "sources_json": _safe_json_dumps(target.get("sources", []), []),
                    "features_json": _safe_json_dumps(target.get("features", []), []),
                    "attributes_json": _safe_json_dumps(target.get("attributes", []), []),
                    "metadata_json": _safe_json_dumps(target.get("metadata", {}), {}),
                    "type": str(target.get("type") or ""),
                },
            )

            redirect_result = self.redirect_relations(source_id, target_id)
            if not redirect_result.get("success", False):
                return {
                    "success": False,
                    "changed": False,
                    "details": {
                        "operation": "merge_entities",
                        "error": "relation redirect failed",
                        "redirect_result": redirect_result,
                    },
                }

            self._run_write(
                "MATCH (e:Entity {id: $source_id}) DETACH DELETE e",
                {"source_id": source_id},
            )
            self._publish_event("ENTITY_MERGED", {"target_id": target_id, "source_id": source_id})
            return {
                "success": True,
                "changed": True,
                "details": {"operation": "merge_entities", "target_id": target_id, "source_id": source_id},
            }
        except Exception as exc:
            return {
                "success": False,
                "changed": False,
                "details": {"operation": "merge_entities", "error": str(exc)},
            }

    def delete_entity(
        self,
        entity_id: str,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("delete_entity")
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return {"success": False, "changed": False, "details": {"operation": "delete_entity", "error": "empty id"}}
        if not self._entity_exists(entity_id):
            return {
                "success": False,
                "changed": False,
                "details": {"operation": "delete_entity", "error": f"entity not found: {entity_id}"},
            }
        try:
            self._run_write(
                "MATCH (e:Entity {id: $id}) DETACH DELETE e",
                {"id": entity_id},
            )
            self._publish_event("ENTITY_DELETED", {"entity_id": entity_id})
            return {"success": True, "changed": True, "details": {"operation": "delete_entity", "entity_id": entity_id}}
        except Exception as exc:
            return {"success": False, "changed": False, "details": {"operation": "delete_entity", "error": str(exc)}}

    def rename_entity(
        self,
        old_id: str,
        new_id: str,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("rename_entity")
        old_id = str(old_id or "").strip()
        new_id = str(new_id or "").strip()
        if not old_id or not new_id or old_id == new_id:
            return {"success": False, "changed": False, "details": {"operation": "rename_entity", "error": "invalid id"}}
        if not self._entity_exists(old_id):
            return {"success": False, "changed": False, "details": {"operation": "rename_entity", "error": f"entity not found: {old_id}"}}
        if self._entity_exists(new_id):
            return {"success": False, "changed": False, "details": {"operation": "rename_entity", "error": f"target id exists: {new_id}"}}
        try:
            self._run_write(
                "MATCH (e:Entity {id: $old_id}) SET e.id = $new_id, e.name = $new_id",
                {"old_id": old_id, "new_id": new_id},
            )
            self._publish_event("ENTITY_RENAMED", {"old_id": old_id, "new_id": new_id})
            return {"success": True, "changed": True, "details": {"operation": "rename_entity", "old_id": old_id, "new_id": new_id}}
        except Exception as exc:
            return {"success": False, "changed": False, "details": {"operation": "rename_entity", "error": str(exc)}}

    # ------------------------------------------------------------------
    # Relation operations
    # ------------------------------------------------------------------
    def add_relation(
        self,
        subject: str,
        relation: str,
        object: str,
        confidence: float = 1.0,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("add_relation")
        subject = str(subject or "").strip()
        relation = str(relation or "").strip()
        object = str(object or "").strip()
        if not subject or not relation or not object:
            return {"success": False, "changed": False, "details": {"operation": "add_relation", "error": "invalid relation input"}}
        if not self._entity_exists(subject) or not self._entity_exists(object):
            return {"success": False, "changed": False, "details": {"operation": "add_relation", "error": "subject/object entity missing"}}

        relation_id = str(uuid.uuid4())
        query = """
        MATCH (s:Entity {id: $subject}), (o:Entity {id: $object})
        CREATE (s)-[r:RELATED {
          id: $id, relation: $relation, confidence: $confidence, sources_json: $sources_json
        }]->(o)
        RETURN r.id AS id
        """
        try:
            rows = self._run_write(
                query,
                {
                    "subject": subject,
                    "object": object,
                    "id": relation_id,
                    "relation": relation,
                    "confidence": float(confidence),
                    "sources_json": _safe_json_dumps([source_info] if source_info else [], []),
                },
            )
            if not rows:
                return {"success": False, "changed": False, "details": {"operation": "add_relation", "error": "create relation failed"}}
            self._publish_event("RELATION_ADDED", {"subject": subject, "relation": relation, "object": object})
            return {"success": True, "changed": True, "details": {"operation": "add_relation", "relation_id": relation_id}}
        except Exception as exc:
            return {"success": False, "changed": False, "details": {"operation": "add_relation", "error": str(exc)}}

    def delete_relation(
        self,
        relation_id: str,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("delete_relation")
        relation_id = str(relation_id or "").strip()
        if not relation_id:
            return {"success": False, "changed": False, "details": {"operation": "delete_relation", "error": "empty id"}}
        try:
            rows = self._run_read(
                "MATCH ()-[r:RELATED {id: $id}]->() RETURN count(r) AS c",
                {"id": relation_id},
            )
            if not rows or int(rows[0]["c"]) <= 0:
                return {"success": False, "changed": False, "details": {"operation": "delete_relation", "error": "relation not found"}}
            self._run_write(
                "MATCH ()-[r:RELATED {id: $id}]->() DELETE r",
                {"id": relation_id},
            )
            self._publish_event("RELATION_DELETED", {"relation_id": relation_id})
            return {"success": True, "changed": True, "details": {"operation": "delete_relation", "relation_id": relation_id}}
        except Exception as exc:
            return {"success": False, "changed": False, "details": {"operation": "delete_relation", "error": str(exc)}}

    def delete_relations_of_entity(
        self,
        entity_id: str,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("delete_relations_of_entity")
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return {"success": False, "changed": False, "details": {"operation": "delete_relations_of_entity", "error": "empty id"}}
        try:
            rows = self._run_read(
                """
                MATCH (e:Entity {id: $entity_id})-[r:RELATED]-(:Entity)
                RETURN collect(r.id) AS relation_ids
                """,
                {"entity_id": entity_id},
            )
            relation_ids = rows[0].get("relation_ids", []) if rows else []
            self._run_write(
                """
                MATCH (e:Entity {id: $entity_id})-[r:RELATED]-(:Entity)
                DELETE r
                """,
                {"entity_id": entity_id},
            )
            self._publish_event("RELATION_DELETED", {"entity_id": entity_id})
            return {
                "success": True,
                "changed": bool(relation_ids),
                "details": {
                    "operation": "delete_relations_of_entity",
                    "entity_id": entity_id,
                    "deleted_count": len(relation_ids),
                    "deleted_relations": relation_ids,
                },
            }
        except Exception as exc:
            return {
                "success": False,
                "changed": False,
                "details": {"operation": "delete_relations_of_entity", "error": str(exc)},
            }

    def find_relations_by_entities(
        self,
        entity1_id: str,
        entity2_id: str,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("find_relations_by_entities")
        entity1_id = str(entity1_id or "").strip()
        entity2_id = str(entity2_id or "").strip()
        if not entity1_id or not entity2_id:
            return {"success": False, "changed": False, "details": {"operation": "find_relations_by_entities", "error": "invalid entity ids"}}
        query = """
        MATCH (a:Entity)-[r:RELATED]->(b:Entity)
        WHERE (
            (a.id = $e1 AND b.id = $e2) OR (a.id = $e2 AND b.id = $e1)
          )
        RETURN r.id AS id, a.id AS subject, r.relation AS relation, b.id AS object,
               r.confidence AS confidence, r.sources_json AS sources_json
        """
        try:
            rows = self._run_read(
                query,
                {"e1": entity1_id, "e2": entity2_id},
            )
            relations = []
            for row in rows:
                relations.append(
                    {
                        "id": str(row.get("id") or ""),
                        "subject": str(row.get("subject") or ""),
                        "relation": str(row.get("relation") or ""),
                        "object": str(row.get("object") or ""),
                        "confidence": float(row.get("confidence") or 1.0),
                        "sources": _safe_json_loads(row.get("sources_json"), []),
                    }
                )
            return {
                "success": True,
                "changed": False,
                "details": {
                    "operation": "find_relations_by_entities",
                    "entity1_id": entity1_id,
                    "entity2_id": entity2_id,
                    "relations": relations,
                    "count": len(relations),
                },
            }
        except Exception as exc:
            return {
                "success": False,
                "changed": False,
                "details": {"operation": "find_relations_by_entities", "error": str(exc)},
            }

    def delete_all_relations_by_entities(
        self,
        entity1_id: str,
        entity2_id: str,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("delete_all_relations_by_entities")
        find_result = self.find_relations_by_entities(entity1_id, entity2_id)
        if not find_result.get("success", False):
            return find_result
        relation_ids = [r.get("id") for r in find_result.get("details", {}).get("relations", []) if r.get("id")]
        deleted = 0
        for rid in relation_ids:
            res = self.delete_relation(rid)
            if res.get("success", False):
                deleted += 1
        self._publish_event("RELATION_DELETED", {"entity1_id": entity1_id, "entity2_id": entity2_id})
        return {
            "success": deleted == len(relation_ids),
            "changed": deleted > 0,
            "details": {
                "operation": "delete_all_relations_by_entities",
                "entity1_id": entity1_id,
                "entity2_id": entity2_id,
                "deleted_count": deleted,
                "target_count": len(relation_ids),
            },
        }

    def redirect_relations(
        self,
        old_entity_id: str,
        new_entity_id: str,
        source_info: Optional[Dict[str, Any]] = None,
    ) -> CoreResult:
        if not self.store.available:
            return self._disabled_result("redirect_relations")
        old_entity_id = str(old_entity_id or "").strip()
        new_entity_id = str(new_entity_id or "").strip()
        if not old_entity_id or not new_entity_id or old_entity_id == new_entity_id:
            return {"success": False, "changed": False, "details": {"operation": "redirect_relations", "error": "invalid entity ids"}}
        if not self._entity_exists(old_entity_id) or not self._entity_exists(new_entity_id):
            return {"success": False, "changed": False, "details": {"operation": "redirect_relations", "error": "old/new entity missing"}}

        query = """
        MATCH (s:Entity)-[r:RELATED]->(o:Entity)
        WHERE s.id = $old_id OR o.id = $old_id
        RETURN r.id AS id, s.id AS subject, r.relation AS relation, o.id AS object,
               r.confidence AS confidence, r.sources_json AS sources_json
        """
        try:
            rows = self._run_read(
                query,
                {"old_id": old_entity_id},
            )
            if not rows:
                return {
                    "success": True,
                    "changed": False,
                    "details": {"operation": "redirect_relations", "updated_count": 0, "deleted_count": 0},
                }

            rel_ids = [str(r.get("id") or "") for r in rows if r.get("id")]
            if rel_ids:
                self._run_write(
                    "MATCH ()-[r:RELATED]->() WHERE r.id IN $ids DELETE r",
                    {"ids": rel_ids},
                )

            updated_count = 0
            deleted_count = 0
            seen = set()
            for row in rows:
                rel_id = str(row.get("id") or "")
                subj = str(row.get("subject") or "")
                obj = str(row.get("object") or "")
                rel_type = str(row.get("relation") or "")
                if subj == old_entity_id:
                    subj = new_entity_id
                if obj == old_entity_id:
                    obj = new_entity_id
                if subj == obj:
                    deleted_count += 1
                    continue
                key = (subj, rel_type, obj)
                if key in seen:
                    deleted_count += 1
                    continue
                seen.add(key)
                self._run_write(
                    """
                    MATCH (s:Entity {id: $subject}), (o:Entity {id: $object})
                    CREATE (s)-[:RELATED {
                      id: $id, relation: $relation, confidence: $confidence, sources_json: $sources_json
                    }]->(o)
                    """,
                    {
                        "subject": subj,
                        "object": obj,
                        "id": rel_id or str(uuid.uuid4()),
                        "relation": rel_type,
                        "confidence": float(row.get("confidence") or 1.0),
                        "sources_json": str(row.get("sources_json") or "[]"),
                    },
                )
                updated_count += 1

            self._publish_event("RELATIONS_REDIRECTED", {"old_entity_id": old_entity_id, "new_entity_id": new_entity_id})
            return {
                "success": True,
                "changed": updated_count > 0 or deleted_count > 0,
                "details": {
                    "operation": "redirect_relations",
                    "old_entity_id": old_entity_id,
                    "new_entity_id": new_entity_id,
                    "updated_count": updated_count,
                    "deleted_count": deleted_count,
                },
            }
        except Exception as exc:
            return {"success": False, "changed": False, "details": {"operation": "redirect_relations", "error": str(exc)}}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def list_entity_ids(self) -> List[str]:
        if not self.store.available:
            return []
        try:
            rows = self._run_read("MATCH (e:Entity) RETURN e.id AS id")
            ids = [str(r.get("id")).strip() for r in rows if isinstance(r.get("id"), str) and str(r.get("id")).strip()]
            return sorted(set(ids))
        except Exception:
            return []

    def get_entity_count(self) -> int:
        if not self.store.available:
            return 0
        try:
            rows = self._run_read("MATCH (e:Entity) RETURN count(e) AS c")
            return int(rows[0]["c"]) if rows else 0
        except Exception:
            return 0

    def get_relation_count(self) -> int:
        if not self.store.available:
            return 0
        try:
            rows = self._run_read("MATCH ()-[r:RELATED]->() RETURN count(r) AS c")
            return int(rows[0]["c"]) if rows else 0
        except Exception:
            return 0

    def get_kg_stats(self) -> Dict[str, Any]:
        feature_count = 0
        attribute_count = 0
        for entity_id in self.list_entity_ids():
            ok, data = self.get_entity(entity_id)
            if ok and data:
                features = data.get("features", [])
                attributes = data.get("attributes", [])
                if isinstance(features, list):
                    feature_count += len(features)
                if isinstance(attributes, list):
                    attribute_count += len(attributes)
        return {
            "entity_count": self.get_entity_count(),
            "relation_count": self.get_relation_count(),
            "feature_count": feature_count,
            "attribute_count": attribute_count,
            "storage_backend": "neo4j",
            "neo4j_available": bool(self.store.available),
            "workflow_id": self.workflow_id,
            "neo4j_database": self.database or "default",
        }

    @staticmethod
    def _merge_sources(a: Any, b: Any) -> List[Dict[str, Any]]:
        left = a if isinstance(a, list) else []
        right = b if isinstance(b, list) else []
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in left + right:
            if not isinstance(item, dict):
                continue
            key = (
                item.get("dialogue_id"),
                item.get("episode_id"),
                item.get("scene_id"),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    @staticmethod
    def _merge_features(a: Any, b: Any) -> List[Dict[str, Any]]:
        left = a if isinstance(a, list) else []
        right = b if isinstance(b, list) else []
        merged: Dict[str, Dict[str, Any]] = {}
        for item in left + right:
            if not isinstance(item, dict):
                continue
            key = str(item.get("feature") or "").strip()
            if not key:
                continue
            if key not in merged:
                merged[key] = dict(item)
                continue
            current = merged[key]
            current["sources"] = KGBase._merge_sources(current.get("sources"), item.get("sources"))
            try:
                if float(item.get("confidence", 0)) > float(current.get("confidence", 0)):
                    current["confidence"] = item.get("confidence")
            except Exception:
                pass
        return list(merged.values())

    @staticmethod
    def _merge_attributes(a: Any, b: Any) -> List[Dict[str, Any]]:
        left = a if isinstance(a, list) else []
        right = b if isinstance(b, list) else []
        merged: Dict[str, Dict[str, Any]] = {}
        for item in left + right:
            if not isinstance(item, dict):
                continue
            field = str(item.get("field") or "").strip()
            if not field:
                continue
            if field not in merged:
                merged[field] = dict(item)
                values = merged[field].get("values")
                if not isinstance(values, list):
                    merged[field]["values"] = [merged[field].get("value")]
                continue
            current = merged[field]
            current["sources"] = KGBase._merge_sources(current.get("sources"), item.get("sources"))
            values = current.get("values")
            if not isinstance(values, list):
                values = [current.get("value")]
            incoming_val = item.get("value")
            if incoming_val not in values:
                values.append(incoming_val)
            current["values"] = values
            try:
                if float(item.get("confidence", 0)) > float(current.get("confidence", 0)):
                    current["value"] = incoming_val
                    current["confidence"] = item.get("confidence")
            except Exception:
                pass
        return list(merged.values())
