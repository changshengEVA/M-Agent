# Memory Core / KG Core Interface

This `core` package is now minimized to KG entity/relation operations.

- Keep only `KGBase` entity and relation APIs.
- Use Neo4j directly as the storage backend.
- Do not use the old `persistence` file layer.
- Workflow isolation is done only by selecting a per-workflow Neo4j database.
- KG Cypher queries do not add `workflow_id` as a second filter anymore.

## Modules

- `kg_base.py`: main KG APIs (entity/relation CRUD and stats).
- `neo4j_store.py`: Neo4j config loading, connection, database resolution, query execution.
- `__init__.py`: public exports.

## Storage Model

### KG data (Neo4j)

- Entity: `(:Entity)`
- Relation: `(:Entity)-[:RELATED]->(:Entity)`
- Unique constraint: `Entity.id` (unique inside one database)

Notes:
- Each `workflow_id` maps to one Neo4j database, for example `wf_<workflow_id>`.
- Queries run in the selected workflow database only.
- No extra `workflow_id` predicate is used in entity/relation Cypher.

### Local files (non-KG primary storage)

`MemoryCore` still keeps workflow-scoped helper files under:

- `data/memory/<workflow_id>/local_store/entity_library/`
- `data/memory/<workflow_id>/scene/`
- `data/memory/<workflow_id>/dialogues/`
- `data/memory/<workflow_id>/episodes/`

## KGBase APIs

### Entity APIs

- `add_entity(entity_id, entity_type=None, source_info=None)`
- `get_entity(entity_id) -> (success, entity_data | None)`
- `merge_entities(target_id, source_id, source_info=None)`
- `delete_entity(entity_id, source_info=None)`
- `rename_entity(old_id, new_id, source_info=None)`

### Relation APIs

- `add_relation(subject, relation, object, confidence=1.0, source_info=None)`
- `delete_relation(relation_id, source_info=None)`
- `delete_relations_of_entity(entity_id, source_info=None)`
- `find_relations_by_entities(entity1_id, entity2_id, source_info=None)`
- `delete_all_relations_by_entities(entity1_id, entity2_id, source_info=None)`
- `redirect_relations(old_entity_id, new_entity_id, source_info=None)`

### Stats APIs

- `list_entity_ids()`
- `get_entity_count()`
- `get_relation_count()`
- `get_kg_stats()`

## Return Format (CoreResult)

Write operations return:

```python
{
  "success": bool,
  "changed": bool,
  "details": {
    "operation": str,
    "...": "detail"
  }
}
```

If Neo4j is unavailable:

```python
{
  "success": False,
  "changed": False,
  "details": {"operation": "...", "error": "Neo4j is not available"}
}
```

## Neo4j Config

Priority:

- Environment variables:
  - `NEO4J_URL`
  - `NEO4J_USER`
  - `NEO4J_PASSWORD`
  - `NEO4J_DATABASE_TEMPLATE` (default: `wf_{workflow_id}`)
- Otherwise fallback to `config/neo4j.yaml`

Example:

```yaml
url: "neo4j://127.0.0.1:7687"
user_name: "neo4j"
password: "your_password"
database_template: "wf_{workflow_id}"
```
