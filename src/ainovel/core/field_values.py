"""Read and update explicitly configured Entry fields. Missing fields are never created."""

from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, JsonValue, ValidationError

from ainovel.core.fields import FieldDefinition
from ainovel.core.records import Entry


def encode(value: JsonValue, name: str, namespace: str) -> Entry:
    if isinstance(value, dict) and value.get("id"):
        namespace = "record:" + str(value["id"])
    definition = FieldDefinition(
        id=uuid5(NAMESPACE_URL, namespace + ":definition"),
        name=name,
        type="object"
        if isinstance(value, dict)
        else "list"
        if isinstance(value, list)
        else "boolean"
        if isinstance(value, bool)
        else "number"
        if isinstance(value, (int, float))
        else "text",
    )
    identity = (
        UUID(value["id"]) if isinstance(value, dict) and value.get("id") else uuid5(NAMESPACE_URL, namespace)
    )
    if isinstance(value, dict):
        return Entry(
            id=identity,
            name=str(value.get("name", name)),
            definition=definition,
            children=[encode(item, key, namespace + ":" + key) for key, item in value.items()],
        )
    if isinstance(value, list):
        return Entry(
            id=identity,
            name=name,
            definition=definition,
            children=[
                encode(item, str(index + 1), namespace + ":" + str(index)) for index, item in enumerate(value)
            ],
        )
    return Entry(id=identity, name=name, definition=definition, value=value)


def decode(entry: Entry) -> JsonValue:
    if entry.definition and entry.definition.type == "object":
        return {
            (child.definition.name if child.definition else child.name): decode(child)
            for child in entry.children
        }
    if entry.definition and entry.definition.type == "list":
        return [decode(child) for child in entry.children]
    return entry.value


def find(entries: list[Entry], scope: str, key: str) -> Entry | None:
    from ainovel.core.field_templates import definition_id

    identity = definition_id(scope, key)
    return next((entry for entry in entries if entry.definition and entry.definition.id == identity), None)


def get(entries: list[Entry], scope: str, key: str, default: JsonValue) -> JsonValue:
    entry = find(entries, scope, key)
    return decode(entry) if entry else default


def records(entries: list[Entry], scope: str, key: str, model: type[BaseModel]) -> list:
    """Typed readers project explicitly configured fields without altering canonical entries."""
    group = find(entries, scope, key)
    result = []
    for entry in group.children if group else []:
        raw = decode(entry)
        if not isinstance(raw, dict):
            raw = {"name": str(raw) if raw is not None else entry.name}
        data = {key: value for key, value in raw.items() if key in model.model_fields and value is not None}
        data.setdefault("id", entry.id)
        data.setdefault("name", entry.name)
        extras = [
            child
            for child in entry.children
            if (child.definition.name if child.definition else child.name) not in model.model_fields
        ]
        if extras and "custom_fields" in model.model_fields:
            data["custom_fields"] = [*data.get("custom_fields", []), *extras]
        try:
            result.append(model.model_validate(data))
        except ValidationError:
            # New templates may intentionally use a different status/type structure.
            result.append(
                model.model_validate(
                    {
                        "id": entry.id,
                        "name": entry.name,
                        "description": entry.text(),
                        "custom_fields": entry.children,
                    }
                )
            )
    return result


def put(entries: list[Entry], scope: str, key: str, value: object) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", round_trip=True)
    if isinstance(value, list):
        value = [
            item.model_dump(mode="json", round_trip=True) if isinstance(item, BaseModel) else item
            for item in value
        ]
    current = find(entries, scope, key)
    if current is None:
        return
    definition = current.definition
    entry = encode(value, definition.name, f"{scope}:{key}")
    entry = bind_definition(entry, definition)
    if current:
        entry.id = current.id
        entries[entries.index(current)] = entry
    else:
        entries.append(entry)


def bind_definition(entry: Entry, definition: FieldDefinition) -> Entry:
    """Validate values against the field definition already configured by the user."""
    children = entry.children
    value = entry.value
    if definition.type == "object" and value is not None:
        children = [encode(str(value), "name", str(entry.id) + ":name"), *children]
        value = None
    if definition.type == "list" and definition.item:
        children = [bind_definition(child, definition.item) for child in children]
    elif definition.type == "object":
        by_name = {child.name: child for child in definition.children}
        children = [
            bind_definition(child, by_name[child.definition.name])
            if child.definition and child.definition.name in by_name
            else child
            for child in children
        ]
    return Entry.model_validate(
        {**entry.model_dump(round_trip=True), "definition": definition, "children": children, "value": value}
    )

