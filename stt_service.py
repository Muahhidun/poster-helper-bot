"""Speech-to-Text service using OpenAI Whisper"""
import logging
from pathlib import Path
from openai import AsyncOpenAI
from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)


class STTService:
    """Speech-to-Text service using Whisper API"""

    def __init__(self):
        self.client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    async def transcribe(self, audio_file_path: Path) -> str:
        """
        Transcribe audio file to text using Whisper

        Args:
            audio_file_path: Path to audio file (ogg, mp3, m4a, etc.)

        Returns:
            Transcribed text
        """
        try:
            logger.info(f"Transcribing audio file: {audio_file_path}")

            with open(audio_file_path, 'rb') as audio_file:
                transcript = await self.client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ru"  # Russian language hint
                )

            text = transcript.text.strip()
            logger.info(f"✅ Transcription successful: '{text[:100]}...'")
            return text

        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            raise Exception(f"Не удалось распознать голос: {e}")


# Singleton instance
_stt_service = None


def get_stt_service() -> STTService:
    """Get singleton STTService instance"""
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
