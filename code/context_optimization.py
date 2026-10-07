"""Public excerpts: context budgeting and process-local template caching.

Sources: services/ai/chat_service.py and services/ai/concept_map_service.py.
Original method bodies and limits are preserved. BaseAIService inheritance,
provider calls, proprietary prompts and document readers are omitted.
The production HTML template is intentionally excluded; tests supply a fixture.
These mechanisms do not implement provider-side context caching or MCP.
"""
import os


class ChatService:
    """Context-only excerpt; no credentials or provider initialization."""

    CHAT_CONTEXT_LIMIT = 32000
    CHAT_FILE_CONTEXT_LIMIT = 12000
    CHAT_HISTORY_MESSAGES = 12
    CHAT_HISTORY_TOTAL_LIMIT = 9000
    CHAT_HISTORY_USER_ITEM_LIMIT = 1400
    CHAT_HISTORY_ASSISTANT_ITEM_LIMIT = 900

    def _history_content_limit(self, role_value):
        return (
            self.CHAT_HISTORY_USER_ITEM_LIMIT
            if str(role_value or '').strip().lower() == 'user'
            else self.CHAT_HISTORY_ASSISTANT_ITEM_LIMIT
        )

    def _build_history_text(self, compact_history):
        if not compact_history:
            return ''

        selected = []
        total_chars = 0

        for msg in reversed(compact_history[-self.CHAT_HISTORY_MESSAGES:]):
            role_value = str(msg.get('role') or '').strip().lower()
            content_value = str(msg.get('content') or '').strip()
            if not content_value:
                continue

            per_item_limit = self._history_content_limit(role_value)
            clipped_content = content_value[:per_item_limit]
            role_label = 'Estudiante' if role_value == 'user' else 'Nerdie'
            line = f"{role_label}: {clipped_content}"

            if total_chars + len(line) > self.CHAT_HISTORY_TOTAL_LIMIT:
                remaining = self.CHAT_HISTORY_TOTAL_LIMIT - total_chars
                if remaining < 120:
                    break

                trimmed = clipped_content[:max(0, remaining - len(role_label) - 2)]
                if not trimmed:
                    break

                line = f"{role_label}: {trimmed}"
                selected.append(line)
                total_chars += len(line)
                break

            selected.append(line)
            total_chars += len(line)

        if not selected:
            return ''

        selected.reverse()
        return "\nHISTORIAL DE CONVERSACION RECIENTE:\n" + "\n".join(selected) + "\n\n"

    def _normalize_history_piece(self, text):
        return ' '.join(str(text or '').strip().lower().split())

    def _compact_history(self, history):
        if not history:
            return []

        compacted = []
        previous_signature = None

        for msg in history:
            if isinstance(msg, dict):
                role_value = str(msg.get('role') or '').strip().lower()
                content_value = str(msg.get('content') or '').strip()
            else:
                role_value = ''
                content_value = str(msg or '').strip()

            if not content_value:
                continue

            signature = (role_value, self._normalize_history_piece(content_value))
            if signature == previous_signature:
                continue

            compacted.append({'role': role_value, 'content': content_value})
            previous_signature = signature

        return compacted


class ConceptMapService:
    """Template-cache excerpt; the production renderer is excluded."""

    _template_cache = None

    @classmethod
    def _get_template(cls):
        """Lee y cachea la plantilla HTML."""
        if cls._template_cache is None:
            template_path = os.path.join(
                os.path.dirname(__file__), 
                'templates', 
                'concept_map_template.html'
            )
            with open(template_path, 'r', encoding='utf-8') as f:
                cls._template_cache = f.read()
        return cls._template_cache

