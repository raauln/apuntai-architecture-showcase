# Public architecture sample from services/ai/__init__.py
# App-specific imports refer to modules intentionally excluded from this showcase.

"""
Paquete de servicios de IA para ApuntAI.
Exporta todos los servicios y proporciona retrocompatibilidad.
"""
from importlib import import_module


_CLASS_MODULES = {
    'BaseAIService': '.base',
    'NotesGenerator': '.notes_generator',
    'StudyToolsService': '.study_tools',
    'TestService': '.test_service',
    'FlashcardService': '.flashcard_service',
    'SummaryService': '.summary_service',
    'ChatService': '.chat_service',
    'PodcastService': '.podcast_service',
    'ConceptMapService': '.concept_map_service',
    'PresentationService': '.presentation_service',
    'ImageService': '.image_service',
    'SongService': '.song_service',
    'TextGuardService': '.text_guard_service',
    'ExamSimulatorService': '.exam_simulator_service',
}

_ai_service_class = None
_ai_service_instance = None


def _build_ai_service_class():
    """Construye la clase AIService solo cuando se necesita."""
    global _ai_service_class
    if _ai_service_class is None:
        from .notes_generator import NotesGenerator
        from .study_tools import StudyToolsService
        from .chat_service import ChatService
        from .podcast_service import PodcastService
        from .concept_map_service import ConceptMapService
        from .presentation_service import PresentationService
        from .image_service import ImageService
        from .song_service import SongService
        from .text_guard_service import TextGuardService
        from .exam_simulator_service import ExamSimulatorService

        class AIService(NotesGenerator, StudyToolsService, ChatService,
                        PodcastService, ConceptMapService, PresentationService,
            ImageService, SongService, TextGuardService, ExamSimulatorService):
            pass

        _ai_service_class = AIService
    return _ai_service_class


def _get_ai_service_instance():
    """Obtiene/crea la instancia singleton de AIService (lazy)."""
    global _ai_service_instance
    if _ai_service_instance is None:
        _ai_service_instance = _build_ai_service_class()()
    return _ai_service_instance


class LazyAIService:
    """Proxy perezoso para retrasar la carga de IA hasta el primer uso real."""
    def __getattr__(self, name):
        return getattr(_get_ai_service_instance(), name)

    def __setattr__(self, name, value):
        setattr(_get_ai_service_instance(), name, value)

    def __repr__(self):
        return '<LazyAIService proxy>'


ai_service = LazyAIService()


def __getattr__(name):
    """Retrocompatibilidad: exporta clases sin importarlas en arranque."""
    if name == 'AIService':
        return _build_ai_service_class()

    module_name = _CLASS_MODULES.get(name)
    if module_name:
        module = import_module(module_name, __name__)
        return getattr(module, name)

    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = [
    'BaseAIService',
    'NotesGenerator',
    'StudyToolsService',
    'TestService',
    'FlashcardService', 
    'SummaryService',
    'ChatService',
    'PodcastService',
    'ConceptMapService',
    'PresentationService',
    'ImageService',
    'SongService',
    'TextGuardService',
    'ExamSimulatorService',
    'AIService',
    'ai_service'
]

