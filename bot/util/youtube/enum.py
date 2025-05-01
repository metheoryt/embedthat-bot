from enum import StrEnum


class SourceLang(StrEnum):
    # https://github.com/FOSWLY/vot-cli/wiki/%5BRU%5D-Supported-langs
    RU = 'ru'
    EN = 'en'
    ZH = 'zh'  # chinese
    KO = 'ko'  # korean
    AR = 'ar'  # arabic
    FR = 'fr'  # french
    IT = 'it'  # italian
    ES = 'es'  # spanish
    DE = 'de'  # german
    JA = 'ja'  # japanese
    MISSING = 'missing'  # not detected


class TargetLang(StrEnum):
    # https://github.com/FOSWLY/vot-cli/wiki/%5BRU%5D-Supported-langs
    RU = 'ru'
    EN = 'en'
    KK = 'kk'  # kazakh
    ORIGINAL = 'original'  # no translation
