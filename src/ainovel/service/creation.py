"""Creation is a reviewable proposal, with visible coverage instead of a hidden interview counter."""

from ainovel.core.domain import CreationField, InterviewResult


class CreationProgress:
    def enrich(self, result: InterviewResult) -> InterviewResult:
        proposal = result.proposal
        known = result.checklist
        fields: list[CreationField] = []
        for key, label, required in (
            ("world", "世界观", True),
            ("characters", "初始角色", False),
            ("preferences", "喜欢的文风与情节", False),
            ("dislikes", "不喜欢的写法", False),
        ):
            declared = next((item for item in known if item.key == key), None)
            if key == "world":
                summary = proposal.world.premise if proposal else ""
                present = bool(proposal and (summary or proposal.world.entries))
            elif key == "characters":
                summary = "、".join(character.name for character in proposal.characters) if proposal else ""
                present = bool(summary)
            else:
                entries = (
                    (proposal.preferences.likes if key == "preferences" else proposal.preferences.dislikes)
                    if proposal
                    else []
                )
                summary = "；".join(item.name for item in entries)
                present = bool(entries)
            status = (
                "defined"
                if present
                else "deferred"
                if declared and declared.status == "deferred" and not required
                else "missing"
            )
            fields.append(
                CreationField(
                    key=key,
                    label=label,
                    required=required,
                    status=status,
                    summary=summary
                    or (declared.summary if declared and status == "deferred" else "尚未定义"),
                )
            )
        result.checklist = fields
        return result
