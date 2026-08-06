"""화면에 뜨는 텍스트가 클라이언트 폰트로 그려지는지 본다.

**이 검사가 없으면 폰에 올려야만 알 수 있는 버그가 된다.** 스키마도 통과하고 어휘도 맞고
길이도 맞는데, 폰트 아틀라스에 없는 글자 하나 때문에 문장이 네모로 깨진다.

허용 집합은 `ProjectBible.text_charset`이 소유한다. 넓히고 싶으면 설정을 고치고
클라이언트 아틀라스를 같은 값으로 다시 굽는다 — 그 값은 `unity_range()`가 만들어 준다.
"""

from daily_special.domain.bible import ProjectBible
from daily_special.domain.issue import Issue, Severity


def check_charset(value: str, field: str, bible: ProjectBible) -> list[Issue]:
    """허용 집합 밖 문자를 ERROR로 돌려준다.

    ERROR인 이유는 **그대로 쓰면 게임이 잘못 돌기** 때문이다(규약 5-4). 읽을 수 없는
    문장은 없는 문장과 같다.

    메시지는 재생성 프롬프트에 그대로 실린다. 그래서 "무엇이 틀렸다"가 아니라
    **"대신 무엇을 쓰라"**까지 적는다.
    """
    offenders = bible.text_charset.violations(value)
    if not offenders:
        return []

    rendered = ", ".join(f"'{char}'(U+{ord(char):04X})" for char in offenders)

    return [
        Issue(
            severity=Severity.ERROR,
            field=field,
            message=(
                f"클라이언트 폰트에 없는 문자가 있다: {rendered}. "
                "그대로 두면 화면에서 네모로 표시된다. "
                "한글·영문·숫자와 기본 문장부호만 쓴다 — "
                "특수 기호가 필요하면 마침표나 쉼표로 대신한다"
            ),
        )
    ]
