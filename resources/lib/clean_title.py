# -*- coding: utf-8 -*-
"""
clean_title.py
--------------
Centralizované čištění Webshare názvů souborů na "čistý titul" -
takový, aby ho TMDB / ČSFD našly v search API.

Inspirováno chováním Stream Cinema. Plugin to používá na třech místech:

    1) api_webshare._normalize_title  - pro grouping (rozpoznat duplicity)
    2) tmdb._strip_title              - pro TMDB search query
    3) csfd                           - pro ČSFD search query

Veřejné rozhraní:
    clean_title(name)        -> str  - vyčistí název na hledatelný titul
    extract_year(name)       -> int | None - rok 19xx/20xx ze jména
    extract_season_episode(name) -> (s:int, e:int) | (None, None)
    is_series_name(name)     -> bool - obsahuje SxxEyy marker?

Příklady:
    "Joker.2019.1080p.BluRay.x264.CZ.mkv"     -> "Joker"
    "The.Mandalorian.S02E05.720p.WEB-DL.mkv"  -> "The Mandalorian"
    "Inception (2010) [1080p] CZ dabing.mp4"  -> "Inception"
"""

from __future__ import annotations

import re
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Regexy
# ---------------------------------------------------------------------------

# Přípony souborů (mkv, mp4, avi, srt, ...) na konci.
_EXT_RE = re.compile(r"\.[a-zA-Z0-9]{2,5}$")

# Rozlišení a obecné quality / source / codec / audio tagy.
# Pokryti vsech beznych scene tagu, aby cisty titul stejny jako Stream Cinema.
_QUALITY_RE = re.compile(
    r"\b(?:"
    # Rozlisseni
    r"2160p|1080p|720p|576p|480p|360p|240p|"
    r"4k|uhd|fhd|hd|sd|qhd|"
    # HDR + barvy
    r"hdr|hdr10|hdr10\+|dolby[-_ ]?vision|dv|sdr|10bit|8bit|"
    # Source
    r"webrip|web[-_]?dl|web[-_ ]?rip|web|"
    r"bluray|blu[-_ ]?ray|brrip|bd|bdrip|bd[-_ ]?remux|remux|"
    r"dvdrip|dvdscr|dvd[-_ ]?r|dvd|"
    r"hdrip|hdtv|tvrip|hdcam|"
    r"cam|camrip|hq[-_ ]?cam|ts|tc|telesync|telecine|"
    r"hdts|hd[-_ ]?ts|"
    r"vhsrip|vhs|"
    # Video codec
    r"x264|x265|h\.?264|h\.?265|hevc|avc|"
    r"xvid|divx|"
    r"mpeg[-_]?2|mpeg[-_]?4|mpeg|vp9|av1|"
    # Audio codec/kanaly
    r"aac|aac2\.0|aac5\.1|"
    r"ac3|eac3|e[-_ ]?ac3|dd|dd5\.?1|dd7\.?1|ddp|ddp5\.?1|"
    r"dts|dts[-_ ]?hd|dts[-_ ]?x|"
    r"atmos|truehd|"
    r"mp3|mp2|opus|flac|vorbis|"
    r"5\.1|7\.1|2\.0|stereo|mono|surround|"
    # Special cuts / brands
    r"remastered|extended|director'?s[-_ ]?cut|directors[-_ ]?cut|"
    r"theatrical|theatrical[-_ ]?cut|unrated|imax|"
    r"proper|repack|internal|limited|complete|"
    r"final[-_ ]?cut|alternative[-_ ]?cut|"
    # Multi-disc / parts
    r"cd[12345]|disc[12345]|part[12345]|"
    # Misc scene tags
    r"hq|hd[-_ ]?rip|web[-_ ]?dl|"
    r"open[-_ ]?matte|ima[xX]"
    r")\b",
    re.IGNORECASE,
)

# Rok 19xx / 20xx jako samostatné "slovo".
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")

# Jazyk / dabing / titulky / AI překlad markery.
# Rozsireno o samostatne "tit", AI/strojove preklady, "vlozeny", "translator"
# a dalsi typicke WS markery, ktere zustaly v nazvech a sabotovaly TMDB match.
_LANG_RE = re.compile(
    r"\b(?:"
    # CZ/SK
    r"cz|sk|cz[-/_ ]?sk|sk[-/_ ]?cz|"
    r"czech|slovak|"
    r"dab|dabing|dabbed|dabovan[ýáéí]|dabovano|dabingom|"
    # Titulky variants (vcetne samostatneho 'tit' co dosud chybelo)
    r"tit|cest(?:it|titulky|sub)|sktit|titulky|titulkami|titules|"
    r"titles|subs?|subtitles|hardsub|softsub|"
    # Forced subtitles markery (v0.0.57) - Webshare casto pridava
    # "+ forced" do nazvu pro forced subs varianty stejneho filmu.
    # Bez tohoto se TMDB i ČSFD search rozbije fuzzy matchem.
    r"forced|forcedsub|forcedsubs|forced[-_ ]?cz|forced[-_ ]?subs?|"
    r"embedded|embed|burnt[-_ ]?in|hardcoded|"
    # AI / strojove preklady / vlozene titulky (WS scene novinky 2024+)
    # Pokryti diakritickych variant: ž/z, é/e, ý/y, á/a, í/i.
    r"vlo[zž]en[éýáíy]|vlo[zž]en[ya]|"
    r"transl(?:ate|ator|ated|ation)?|trans|"
    r"p[rř]ekl(?:ad|adan[éýaí]|adan[ya])?|p[rř]eklad|p[rř]eloz\w*|p[rř]elo[zž]\w*|"
    r"ai[-_ ]?titulky|ai[-_ ]?tit|ai[-_ ]?subs?|"
    r"strojov[éýaí]|strojov[ya]|machine[-_ ]?translat\w*|"
    r"auto[-_ ]?titulky|auto[-_ ]?tit|auto[-_ ]?subs?|"
    # Polsky (lektor PL, dubbing PL, napisy PL)
    r"lektor|pl|polski|polsky|polish|pol|napisy|dubbing|"
    # Anglicky
    r"en(?:g)?|eng?lish|"
    # Ostatni jazyky (release tagy v Webshare scene)
    r"ger|german|deutsch|de|"
    r"rus|russian|ru|"
    r"hun|hungarian|hu|"
    r"fre|french|fr|"
    r"spa|spanish|esp|es|"
    r"ita|italian|it|"
    r"ukr|ukrainian|ua|"
    r"jp|jpn|japanese|"
    # Generic
    r"dual|multi|original|orig|"
    r"csfd|imdb"
    r")\b",
    re.IGNORECASE,
)

# Známé release groups + ostatní šum + streamovaci platformy.
_GROUP_RE = re.compile(
    r"\b(?:"
    # Klasicke release groups
    r"YIFY|YTS|RARBG|FGT|EVO|NTb|KiNGS|ION10|TGx|"
    r"PSA|GalaxyRG|MeGusta|FLUX|EZTV|"
    r"OFTRiNG|RBG|sujaidr|nokia|kogal|"
    r"SPARKS|GECKOS|AMIABLE|ROVERS|DEFLATE|TBS|"
    r"WEBPATCH|UTR|CMRG|JFKXL|"
    # Platforms (Amazon, Netflix, Disney+, HBO Max, iTunes...)
    r"AMZN|NF|DSNP|DSNY|MAX|HMAX|HBO|ITUNES|ATVP|APPLE|"
    r"PCOK|PMTP|PARAMOUNT|HULU|STAN|CRAVE|VUDU|"
    # Kontejnery
    r"mkv|mp4|avi|m4v|wmv|mov|flv|webm|ogm|"
    # Random scene noise
    r"obfuscated|scene|rb|rbg"
    r")\b",
    re.IGNORECASE,
)

# SxxEyy patterny (s01e02, S01E02, 1x02, 01x02).
_SE_RE = re.compile(r"\b[Ss](\d{1,2})\s*[EeXx]\s*(\d{1,3})\b")
_SE_ALT_RE = re.compile(r"\b(\d{1,2})\s*[xX]\s*(\d{1,3})\b")


# ---------------------------------------------------------------------------
# Veřejné API
# ---------------------------------------------------------------------------

def extract_year(name: str) -> Optional[int]:
    """Najde rok 19xx / 20xx v názvu. Vrátí int nebo None."""
    if not name:
        return None
    m = _YEAR_RE.search(name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def extract_season_episode(name: str) -> Tuple[Optional[int], Optional[int]]:
    """Vrátí (season, episode) nebo (None, None) pokud není SxxEyy ani 1x02."""
    if not name:
        return None, None
    m = _SE_RE.search(name) or _SE_ALT_RE.search(name)
    if not m:
        return None, None
    try:
        return int(m.group(1)), int(m.group(2))
    except ValueError:
        return None, None


def is_series_name(name: str) -> bool:
    """True pokud název obsahuje SxxEyy marker (= epizoda seriálu)."""
    if not name:
        return False
    return bool(_SE_RE.search(name) or _SE_ALT_RE.search(name))


def detect_media_type(name: str) -> str:
    """
    Automaticky rozpozná typ obsahu z názvu souboru.

    Heuristika:
      - obsahuje SxxEyy / 1x02   -> "series"
      - obsahuje "Season X"      -> "series"
      - obsahuje "Series" + číslo-> "series"
      - jinak                    -> "movie"

    Vrací: "movie" | "series"
    """
    if not name:
        return "movie"
    if is_series_name(name):
        return "series"
    if re.search(r"\b(season|s\u00e9rie|rocnik|ro\u010dn\u00edk)\s*\d+", name, re.IGNORECASE):
        return "series"
    return "movie"


def clean_title(name: str, *, keep_series_marker: bool = False) -> str:
    """
    Vyčistí Webshare název souboru na čistý titul.

    v0.0.58: CUT-AT-FIRST-MARKER strategie (Stream Cinema-style).
    Místo postupného odstraňování tokenů (které nechávalo zbytky jako
    "Vyšehrad Fylm 5 1" z "Vyšehrad.Fylm.2022.1080p.CZ.5.1.mkv") se
    najde POZICE prvního scene markeru (rok/quality/jazyk/SxxEyy/'+')
    a vezme se jen text PŘED ním. Tím spolehlivě dropneme vše za:
        - rokem (19xx/20xx)
        - SxxEyy / 1x02
        - rozlišením/codecem (1080p, x264, ...)
        - jazykovým tagem (CZ, dabing, forced, ...)
        - release group (RARBG, YIFY, ...)
        - separatorem '+' (Webshare "Film + tit")

    :param keep_series_marker: pokud True, SxxEyy zůstane v názvu
                               (užitečné pro grouping epizod).

    Příklady:
        "Vyšehrad.Fylm.2022.1080p.CZ.5.1.mkv"   -> "Vyšehrad Fylm"
        "Joker.2019.1080p.BluRay.x264.CZ.mkv"   -> "Joker"
        "Pelíšky.cz.dabing.+.tit.mkv"           -> "Pelíšky"
        "Inception (2010) [1080p] CZ.mp4"       -> "Inception"
        "The.Mandalorian.S02E05.WEB-DL.mkv"     -> "The Mandalorian"
    """
    if not name:
        return ""

    s = name

    # 1) Přípona
    s = _EXT_RE.sub("", s)

    # 2) Tečky/podtržítka/pomlčky/závorky → mezery (PŘED hledáním markerů,
    #    aby _QUALITY_RE\b atd. matchovaly i 'Joker.2019' style).
    s = s.replace(".", " ").replace("_", " ").replace("-", " ")
    s = re.sub(r"[\(\)\[\]\{\}]", " ", s)

    # 2b) Specialni kombinace AI prekladu (PRED hledanim markeru) - smaze
    #     cely blok 'tit AI', 'AI tit', 'CZ AI', 'machine translated' atd.
    _AI_COMBO_RE = re.compile(
        r"\b(?:tit\s+ai|ai\s+tit|ai\s+titulky|ai\s+subs?|"
        r"cz\s+ai|ai\s+cz|sk\s+ai|ai\s+sk|"
        r"machine\s+translat\w*|google\s+translat\w*)\b",
        re.IGNORECASE,
    )
    s = _AI_COMBO_RE.sub(" ", s)

    # 3) NEW v0.0.58: Najdi POZICI prvního scene markeru a odřízni vše
    #    za ním (včetně markeru samotného). Tím se odbouraji "Fylm 5 1"
    #    rezidua po _QUALITY/_LANG sub() z minulých verzí.
    cut_positions = []

    # rok (19xx / 20xx)
    m = _YEAR_RE.search(s)
    if m:
        cut_positions.append(m.start())

    # SxxEyy / 1x02 (jen pro filmy; seriály si nechají marker)
    if not keep_series_marker:
        m = _SE_RE.search(s) or _SE_ALT_RE.search(s)
        if m:
            cut_positions.append(m.start())

    # quality / source / codec / audio
    m = _QUALITY_RE.search(s)
    if m:
        cut_positions.append(m.start())

    # jazyk / dabing / titulky / forced / lektor
    m = _LANG_RE.search(s)
    if m:
        cut_positions.append(m.start())

    # release group (RARBG, YIFY, AMZN, ...)
    m = _GROUP_RE.search(s)
    if m:
        cut_positions.append(m.start())

    # Webshare '+' separator ("Film + tit", "Film + forced")
    m = re.search(r"\s\+\s", s)
    if m:
        cut_positions.append(m.start())

    # Vezmi NEJBLIŽŠÍ marker - to je hranice mezi "titulem" a "scene crud"
    if cut_positions:
        cut = min(cut_positions)
        s = s[:cut]

    # 4) Druhý průchod _LANG_RE/_QUALITY_RE/_GROUP_RE - pro případ že
    #    marker byl uvnitř titulu (vzácné: "The 6th Day" - "6th" může
    #    být v _SE_ALT_RE jako "6x?" - tady to nevadí, ale jistota).
    #    Tady už neořezáváme prefix, jen substitujeme zbytky.
    s = _QUALITY_RE.sub(" ", s)
    s = _LANG_RE.sub(" ", s)
    s = _GROUP_RE.sub(" ", s)

    # 5) Whitespace + finální strip
    s = re.sub(r"\s+", " ", s).strip(" .-_,;:+")

    # 5b) v0.0.63: marketingove suffixy - 'Fylm' (scene typo, "Vysehrad
    #     Fylm"), 'The Movie' jen jako koncovy suffix po separatoru.
    #     Drive klin do TMDB/Webshare search: "Vysehrad Fylm" nikdy
    #     nematchnul.
    #
    #     POZOR: 'Film' a 'Movie' SOLO NESTRIPUJEME - "Scary Movie",
    #     "The Movie Hero" jsou legitimni tituly. "Fylm" je scene typo
    #     bez realneho ekvivalentu. "Title - The Movie" / "Title: The
    #     Movie" pattern je marketing decoration od distributorov.
    _MARKETING_SUFFIX_RE = re.compile(
        r"\s*[-:]\s*the\s+movie\s*$",
        re.IGNORECASE,
    )
    _FYLM_GLOBAL_RE = re.compile(r"\b(fylm)\b", re.IGNORECASE)

    def _normalize(text: str) -> str:
        # Sjednoceni: kazdy sub() muze nechat double-spaces nebo prazdny
        # konec/zacatek. Tahle helper normalizuje na clean single-space
        # variantu se stripem separator znaku.
        return re.sub(r"\s+", " ", text).strip(" .-_,;:+")

    cand = _normalize(_MARKETING_SUFFIX_RE.sub("", s))
    if re.search(r"[A-Za-z\u00c0-\u017f]{2,}", cand):
        s = cand

    cand = _normalize(_FYLM_GLOBAL_RE.sub(" ", s))
    if re.search(r"[A-Za-z\u00c0-\u017f]{2,}", cand):
        s = cand

    # 6) Vyhodi zbyle "samostatna cisla na konci" (typicky "Vyšehrad 5 1"
    #    z audio "5.1" co prosakl pres cut, nebo "Joker 02"). Bezpecnostni
    #    gate: po orezu musi zustat aspon 1 pismenne slovo s 2+ znaky.
    JUNK_END = re.compile(r"\s+\d{1,3}$")
    while True:
        m = JUNK_END.search(s)
        if not m:
            break
        candidate = s[:m.start()].strip(" .-_,;:")
        if not re.search(r"[A-Za-z\u00c0-\u017f]{2,}", candidate):
            break
        s = candidate

    return s


# ---------------------------------------------------------------------------
# v0.0.58: Aggressive fallback helpers pro retry search
# ---------------------------------------------------------------------------

# Mapa diakritika → ASCII (pro ČSFD/TMDB search URL fallback).
# unicodedata.normalize('NFKD') by stačil, ale Kodi Python 3 občas
# nemá full Unicode tabulky - tahle ruční mapa pokrývá CZ + SK + PL.
_DIACRITIC_MAP = str.maketrans({
    "á": "a", "č": "c", "ď": "d", "é": "e", "ě": "e",
    "í": "i", "ň": "n", "ó": "o", "ř": "r", "š": "s",
    "ť": "t", "ú": "u", "ů": "u", "ý": "y", "ž": "z",
    "Á": "A", "Č": "C", "Ď": "D", "É": "E", "Ě": "E",
    "Í": "I", "Ň": "N", "Ó": "O", "Ř": "R", "Š": "S",
    "Ť": "T", "Ú": "U", "Ů": "U", "Ý": "Y", "Ž": "Z",
    "ä": "a", "ö": "o", "ü": "u", "ß": "s",
    "ł": "l", "ą": "a", "ę": "e", "ć": "c", "ń": "n",
    "ś": "s", "ź": "z", "ż": "z",
    "à": "a", "â": "a", "ç": "c", "è": "e", "ê": "e",
    "ë": "e", "î": "i", "ï": "i", "ô": "o", "û": "u",
    "ñ": "n",
})


def ascii_fold(s: str) -> str:
    """
    Sundá českou/slovenskou/polskou diakritiku - "Pelíšky" -> "Pelisky",
    "Vyšehrad" -> "Vysehrad". ČSFD search někdy lépe matchuje ASCII formu
    (záleží jak Cloudflare cachuje URL).
    """
    if not s:
        return ""
    try:
        return s.translate(_DIACRITIC_MAP)
    except Exception:  # noqa: BLE001
        return s


def letters_only_title(name: str) -> str:
    """
    Nejagresivnější varianta pro fallback search query: nechá JEN
    písmena a mezery. Žádné číslice, žádné '+'/'-'/'_'. Užitečné když
    první search vrátí 0 výsledků a tipujeme že tam pořád zbývá scene
    crud (typové zbytky čísel z audio kanálů, sezónní marker, "Top 10"
    atd.).

    Cena: ztratíme "21" v "21 Jump Street" nebo "2" v "Top Gun 2".
    Proto se používá JEN jako 2. pokus.

    "Vyšehrad 2 1080" -> "Vyšehrad"
    "Top Gun 2"       -> "Top Gun"   (true negative)
    "Joker"           -> "Joker"     (neutral)
    """
    if not name:
        return ""
    # Necháme pismena (vč. diakritiky) a mezery, vše ostatní pryč.
    s = re.sub(r"[^A-Za-z\u00c0-\u017f\s]", " ", name)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Pattern pro detekci ceske diakritiky - rozhoduje o CZ/EN split
_CZ_DIACRITIC_RE = re.compile(
    r"[\u00e1\u010d\u010f\u00e9\u011b\u00ed\u0148\u00f3\u0159"
    r"\u0161\u0165\u00fa\u016f\u00fd\u017e"
    r"\u00c1\u010c\u010e\u00c9\u011a\u00cd\u0147\u00d3\u0158"
    r"\u0160\u0164\u00da\u016e\u00dd\u017d]"
)


def title_split_variants(clean: str) -> list:
    """
    v0.0.59: Pro vicewordove tituly (CZ+EN concatenation, scene crud,
    nebo prebytecne stop-words) vrati seznam zkracenych variant pro
    fallback search retry. Pouziva se KDYZ vsechny ostatni pokusy
    selhali a mame stale 0 vysledku.

    Tri pristupy v poradi priority:

    1) CZ/EN diacritic-transition split - typicke pro Webshare:
       "V úkrytu Shelter"   -> ["V úkrytu", "Shelter"]
       "Pelíšky Cosy Dens"  -> ["Pelíšky", "Cosy Dens"]
       (najde POSLEDNI slovo s diakritikou - tam je hranice CZ->EN)

    2) Progressive trim from END (drop last word postupne):
       "Toy Story Historicky"  -> ["Toy Story", "Toy"]
       "X-Men Origins Wolverine" -> ["X-Men Origins", "X-Men"]

    3) Progressive trim from START (drop first word postupne):
       "Toy Story Historicky"  -> ["Story Historicky", "Historicky"]

    Jednoslovne tituly vrati prazdny list ([] - neni co rozdelovat).

    Pouziti:
        for variant in title_split_variants(clean):
            results = search(variant)
            if results:
                break  # zachran misi
    """
    if not clean:
        return []
    words = clean.split()
    if len(words) < 2:
        return []

    seen: set = set()
    out: list = []

    def _add(s: str) -> None:
        s2 = s.strip()
        if not s2 or len(s2) < 3:
            return
        sl = s2.lower()
        if sl in seen:
            return
        seen.add(sl)
        out.append(s2)

    # 1) CZ/EN diacritic-transition split.
    # Najdi POSLEDNI slovo s ceskou diakritikou - hranice CZ->EN.
    last_cz_idx = -1
    for i, w in enumerate(words):
        if _CZ_DIACRITIC_RE.search(w):
            last_cz_idx = i

    if 0 <= last_cz_idx < len(words) - 1:
        cz_part = " ".join(words[:last_cz_idx + 1])
        en_part = " ".join(words[last_cz_idx + 1:])
        _add(cz_part)
        _add(en_part)

    # 2) Progressive trim from end
    for n in range(len(words) - 1, 0, -1):
        _add(" ".join(words[:n]))

    # 3) Progressive trim from start (drop short stopwords like "V", "do", "z")
    for n in range(1, len(words)):
        suffix = " ".join(words[n:])
        # Skip 1-2 letter prefixes (jen prepokojeni "the" stopwords)
        if len(suffix) >= 4:
            _add(suffix)

    return out


def clean_series_name(name: str) -> str:
    """
    Pro seriály: vrátí jméno seriálu BEZ SxxEyy a všeho za ním.

    "Game.of.Thrones.S01E02.720p.CZ.mkv" -> "Game of Thrones"
    "The Mandalorian S02E05 1080p"       -> "The Mandalorian"
    """
    return clean_title(name, keep_series_marker=False)


def episode_base_title(name: str) -> str:
    """
    Pro epizodu: vrátí titul ZAVŘENÝ SxxEyy markerem (ne dál).

    "Game.of.Thrones.S01E02.720p.CZ.mkv" -> "Game of Thrones S01E02"
    """
    if not name:
        return ""
    s = _EXT_RE.sub("", name)
    m = _SE_RE.search(s) or _SE_ALT_RE.search(s)
    if not m:
        return clean_title(name)
    # vezmi vše až po SxxEyy (včetně)
    s = s[:m.end()]
    # vyčisti kvality (které by mohly být před SxxEyy)
    s = _QUALITY_RE.sub(" ", s)
    s = _LANG_RE.sub(" ", s)
    s = _GROUP_RE.sub(" ", s)
    s = s.replace(".", " ").replace("_", " ").replace("-", " ")
    s = re.sub(r"[\(\)\[\]\{\}]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .-_,;:")
    return s
