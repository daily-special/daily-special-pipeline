"""파이프라인 전역 예외 계층."""


class DailySpecialError(Exception):
    """이 파이프라인이 던지는 모든 예외의 뿌리."""


class ConfigError(DailySpecialError):
    """설정 데이터(ProjectBible)의 불변식 위반.

    생성물의 규칙 위반은 예외가 아니라 Issue로 모은다. 설정은 다르다 —
    설정이 틀리면 파이프라인 전 구간이 조용히 틀린 채로 통과하므로 즉시 멈춘다.
    """


class LlmError(DailySpecialError):
    """LLM 전송 실패. 어댑터가 재시도를 소진한 뒤에도 실패한 경우다.

    검증 실패로 인한 재생성은 이 예외를 쓰지 않는다. 그쪽은 서비스가 Issue를 보고 판단한다.
    """
