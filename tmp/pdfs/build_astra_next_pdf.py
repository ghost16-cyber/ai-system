from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Iterable, Sequence
from xml.sax.saxutils import escape

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
from PIL import Image as PILImage
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path("/home/palla/projects/ai-system-1")
TMP = ROOT / "tmp" / "pdfs"
OUT = ROOT / "output" / "pdf"
PDF_PATH = OUT / "Astra_Next_Research_Blueprint_v1.1.pdf"

NAVY = HexColor("#102A43")
DEEP_NAVY = HexColor("#071A2B")
BLUE = HexColor("#2D6CDF")
TEAL = HexColor("#008C95")
MINT = HexColor("#DDF4F1")
GOLD = HexColor("#D19A2A")
PALE_GOLD = HexColor("#F8EDCF")
INK = HexColor("#1C2733")
MUTED = HexColor("#5D6B78")
LIGHT = HexColor("#F3F6F8")
LINE_COLOR = HexColor("#CDD7DF")
GREEN = HexColor("#2E7D5B")
RED = HexColor("#A23B3B")
PURPLE = HexColor("#6E56CF")
WHITE = colors.white

PAGE_W, PAGE_H = letter
MARGIN_L = 0.78 * inch
MARGIN_R = 0.72 * inch
MARGIN_T = 0.72 * inch
MARGIN_B = 0.72 * inch
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R


def register_fonts() -> tuple[str, str, str, str]:
    candidates = [
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Italic.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
        ),
    ]
    for regular, bold, italic, mono in candidates:
        if all(Path(path).exists() for path in (regular, bold, italic, mono)):
            pdfmetrics.registerFont(TTFont("AstraSans", regular))
            pdfmetrics.registerFont(TTFont("AstraSans-Bold", bold))
            pdfmetrics.registerFont(TTFont("AstraSans-Italic", italic))
            pdfmetrics.registerFont(TTFont("AstraMono", mono))
            return "AstraSans", "AstraSans-Bold", "AstraSans-Italic", "AstraMono"
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Courier"


FONT, FONT_BOLD, FONT_ITALIC, FONT_MONO = register_fonts()


def make_styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "AstraBody",
            parent=sample["BodyText"],
            fontName=FONT,
            fontSize=9.25,
            leading=13.2,
            textColor=INK,
            alignment=TA_JUSTIFY,
            spaceAfter=7,
            allowWidows=0,
            allowOrphans=0,
        ),
        "body_left": ParagraphStyle(
            "AstraBodyLeft",
            parent=sample["BodyText"],
            fontName=FONT,
            fontSize=9.25,
            leading=13.2,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=7,
        ),
        "small": ParagraphStyle(
            "AstraSmall",
            parent=sample["BodyText"],
            fontName=FONT,
            fontSize=7.7,
            leading=10.4,
            textColor=MUTED,
            spaceAfter=5,
        ),
        "caption": ParagraphStyle(
            "AstraCaption",
            parent=sample["BodyText"],
            fontName=FONT,
            fontSize=7.7,
            leading=10.2,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceBefore=3,
            spaceAfter=9,
        ),
        "h1": ParagraphStyle(
            "Heading1",
            parent=sample["Heading1"],
            fontName=FONT_BOLD,
            fontSize=20,
            leading=23,
            textColor=NAVY,
            spaceBefore=15,
            spaceAfter=10,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "Heading2",
            parent=sample["Heading2"],
            fontName=FONT_BOLD,
            fontSize=13.2,
            leading=16,
            textColor=BLUE,
            spaceBefore=13,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "Heading3",
            parent=sample["Heading3"],
            fontName=FONT_BOLD,
            fontSize=10.5,
            leading=13,
            textColor=TEAL,
            spaceBefore=9,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "chapter_number": ParagraphStyle(
            "ChapterNumber",
            fontName=FONT_BOLD,
            fontSize=10,
            leading=12,
            textColor=GOLD,
            alignment=TA_LEFT,
            spaceAfter=9,
            uppercase=True,
        ),
        "chapter_title": ParagraphStyle(
            "ChapterTitle",
            fontName=FONT_BOLD,
            fontSize=28,
            leading=32,
            textColor=WHITE,
            alignment=TA_LEFT,
            spaceAfter=18,
        ),
        "chapter_summary": ParagraphStyle(
            "ChapterSummary",
            fontName=FONT,
            fontSize=12,
            leading=17,
            textColor=HexColor("#DCE8F2"),
            alignment=TA_LEFT,
            spaceAfter=12,
        "cover_title": ParagraphStyle(
        "cover_title": ParagraphStyle(
# <<<MISSING_LINE_201>>>
# <<<MISSING_LINE_202>>>
# <<<MISSING_LINE_203>>>
# <<<MISSING_LINE_204>>>
# <<<MISSING_LINE_205>>>
# <<<MISSING_LINE_206>>>
# <<<MISSING_LINE_207>>>
        "cover_subtitle": ParagraphStyle(
# <<<MISSING_LINE_209>>>
# <<<MISSING_LINE_210>>>
# <<<MISSING_LINE_211>>>
# <<<MISSING_LINE_212>>>
# <<<MISSING_LINE_213>>>
# <<<MISSING_LINE_214>>>
# <<<MISSING_LINE_215>>>
# <<<MISSING_LINE_216>>>
# <<<MISSING_LINE_217>>>
# <<<MISSING_LINE_218>>>
# <<<MISSING_LINE_219>>>
# <<<MISSING_LINE_220>>>
# <<<MISSING_LINE_221>>>
# <<<MISSING_LINE_222>>>
# <<<MISSING_LINE_223>>>
# <<<MISSING_LINE_224>>>
# <<<MISSING_LINE_225>>>
# <<<MISSING_LINE_226>>>
# <<<MISSING_LINE_227>>>
# <<<MISSING_LINE_228>>>
# <<<MISSING_LINE_229>>>
# <<<MISSING_LINE_230>>>
# <<<MISSING_LINE_231>>>
# <<<MISSING_LINE_232>>>
# <<<MISSING_LINE_233>>>
# <<<MISSING_LINE_234>>>
# <<<MISSING_LINE_235>>>
# <<<MISSING_LINE_236>>>
# <<<MISSING_LINE_237>>>
# <<<MISSING_LINE_238>>>
# <<<MISSING_LINE_239>>>
# <<<MISSING_LINE_240>>>
# <<<MISSING_LINE_241>>>
# <<<MISSING_LINE_242>>>
# <<<MISSING_LINE_243>>>
# <<<MISSING_LINE_244>>>
# <<<MISSING_LINE_245>>>
# <<<MISSING_LINE_246>>>
# <<<MISSING_LINE_247>>>
# <<<MISSING_LINE_248>>>
# <<<MISSING_LINE_249>>>
# <<<MISSING_LINE_250>>>
# <<<MISSING_LINE_251>>>
# <<<MISSING_LINE_252>>>
# <<<MISSING_LINE_253>>>
# <<<MISSING_LINE_254>>>
# <<<MISSING_LINE_255>>>
# <<<MISSING_LINE_256>>>
# <<<MISSING_LINE_257>>>
# <<<MISSING_LINE_258>>>
# <<<MISSING_LINE_259>>>
# <<<MISSING_LINE_260>>>
# <<<MISSING_LINE_261>>>
# <<<MISSING_LINE_262>>>
# <<<MISSING_LINE_263>>>
# <<<MISSING_LINE_264>>>
# <<<MISSING_LINE_265>>>
# <<<MISSING_LINE_266>>>
# <<<MISSING_LINE_267>>>
# <<<MISSING_LINE_268>>>
# <<<MISSING_LINE_269>>>
# <<<MISSING_LINE_270>>>
# <<<MISSING_LINE_271>>>
# <<<MISSING_LINE_272>>>
# <<<MISSING_LINE_273>>>
# <<<MISSING_LINE_274>>>
# <<<MISSING_LINE_275>>>
# <<<MISSING_LINE_276>>>
# <<<MISSING_LINE_277>>>
# <<<MISSING_LINE_278>>>
# <<<MISSING_LINE_279>>>
# <<<MISSING_LINE_280>>>
# <<<MISSING_LINE_281>>>
# <<<MISSING_LINE_282>>>
# <<<MISSING_LINE_283>>>
# <<<MISSING_LINE_284>>>
# <<<MISSING_LINE_285>>>
# <<<MISSING_LINE_286>>>
# <<<MISSING_LINE_287>>>
# <<<MISSING_LINE_288>>>
# <<<MISSING_LINE_289>>>
# <<<MISSING_LINE_290>>>
# <<<MISSING_LINE_291>>>
# <<<MISSING_LINE_292>>>
# <<<MISSING_LINE_293>>>
# <<<MISSING_LINE_294>>>
# <<<MISSING_LINE_295>>>
# <<<MISSING_LINE_296>>>
# <<<MISSING_LINE_297>>>
# <<<MISSING_LINE_298>>>
# <<<MISSING_LINE_299>>>
# <<<MISSING_LINE_300>>>
# <<<MISSING_LINE_301>>>
# <<<MISSING_LINE_302>>>
# <<<MISSING_LINE_303>>>
# <<<MISSING_LINE_304>>>
# <<<MISSING_LINE_305>>>
# <<<MISSING_LINE_306>>>
# <<<MISSING_LINE_307>>>
# <<<MISSING_LINE_308>>>
# <<<MISSING_LINE_309>>>
# <<<MISSING_LINE_310>>>
# <<<MISSING_LINE_311>>>
# <<<MISSING_LINE_312>>>
# <<<MISSING_LINE_313>>>
# <<<MISSING_LINE_314>>>
# <<<MISSING_LINE_315>>>
# <<<MISSING_LINE_316>>>
# <<<MISSING_LINE_317>>>
# <<<MISSING_LINE_318>>>
# <<<MISSING_LINE_319>>>
# <<<MISSING_LINE_320>>>
# <<<MISSING_LINE_321>>>
# <<<MISSING_LINE_322>>>
# <<<MISSING_LINE_323>>>
# <<<MISSING_LINE_324>>>
# <<<MISSING_LINE_325>>>
# <<<MISSING_LINE_326>>>
# <<<MISSING_LINE_327>>>
# <<<MISSING_LINE_328>>>
# <<<MISSING_LINE_329>>>
# <<<MISSING_LINE_330>>>
# <<<MISSING_LINE_331>>>
# <<<MISSING_LINE_332>>>
# <<<MISSING_LINE_333>>>
# <<<MISSING_LINE_334>>>
# <<<MISSING_LINE_335>>>
# <<<MISSING_LINE_336>>>
# <<<MISSING_LINE_337>>>
# <<<MISSING_LINE_338>>>
# <<<MISSING_LINE_339>>>
# <<<MISSING_LINE_340>>>
# <<<MISSING_LINE_341>>>
# <<<MISSING_LINE_342>>>
# <<<MISSING_LINE_343>>>
# <<<MISSING_LINE_344>>>
# <<<MISSING_LINE_345>>>
# <<<MISSING_LINE_346>>>
# <<<MISSING_LINE_347>>>
# <<<MISSING_LINE_348>>>
# <<<MISSING_LINE_349>>>
# <<<MISSING_LINE_350>>>
# <<<MISSING_LINE_351>>>
# <<<MISSING_LINE_352>>>
# <<<MISSING_LINE_353>>>
# <<<MISSING_LINE_354>>>
# <<<MISSING_LINE_355>>>
# <<<MISSING_LINE_356>>>
# <<<MISSING_LINE_357>>>
# <<<MISSING_LINE_358>>>
# <<<MISSING_LINE_359>>>
# <<<MISSING_LINE_360>>>
# <<<MISSING_LINE_361>>>
# <<<MISSING_LINE_362>>>
# <<<MISSING_LINE_363>>>
# <<<MISSING_LINE_364>>>
# <<<MISSING_LINE_365>>>
# <<<MISSING_LINE_366>>>
# <<<MISSING_LINE_367>>>
# <<<MISSING_LINE_368>>>
# <<<MISSING_LINE_369>>>
# <<<MISSING_LINE_370>>>
# <<<MISSING_LINE_371>>>
# <<<MISSING_LINE_372>>>
# <<<MISSING_LINE_373>>>
# <<<MISSING_LINE_374>>>
# <<<MISSING_LINE_375>>>
# <<<MISSING_LINE_376>>>
# <<<MISSING_LINE_377>>>
# <<<MISSING_LINE_378>>>
# <<<MISSING_LINE_379>>>
# <<<MISSING_LINE_380>>>
# <<<MISSING_LINE_381>>>
# <<<MISSING_LINE_382>>>
# <<<MISSING_LINE_383>>>
# <<<MISSING_LINE_384>>>
# <<<MISSING_LINE_385>>>
# <<<MISSING_LINE_386>>>
# <<<MISSING_LINE_387>>>
# <<<MISSING_LINE_388>>>
# <<<MISSING_LINE_389>>>
# <<<MISSING_LINE_390>>>
# <<<MISSING_LINE_391>>>
# <<<MISSING_LINE_392>>>
# <<<MISSING_LINE_393>>>
# <<<MISSING_LINE_394>>>
# <<<MISSING_LINE_395>>>
# <<<MISSING_LINE_396>>>
# <<<MISSING_LINE_397>>>
# <<<MISSING_LINE_398>>>
# <<<MISSING_LINE_399>>>
# <<<MISSING_LINE_400>>>
# <<<MISSING_LINE_401>>>
# <<<MISSING_LINE_402>>>
# <<<MISSING_LINE_403>>>
# <<<MISSING_LINE_404>>>
# <<<MISSING_LINE_405>>>
# <<<MISSING_LINE_406>>>
# <<<MISSING_LINE_407>>>
# <<<MISSING_LINE_408>>>
# <<<MISSING_LINE_409>>>
# <<<MISSING_LINE_410>>>
# <<<MISSING_LINE_411>>>
# <<<MISSING_LINE_412>>>
# <<<MISSING_LINE_413>>>
# <<<MISSING_LINE_414>>>
# <<<MISSING_LINE_415>>>
# <<<MISSING_LINE_416>>>
# <<<MISSING_LINE_417>>>
# <<<MISSING_LINE_418>>>
# <<<MISSING_LINE_419>>>
# <<<MISSING_LINE_420>>>
# <<<MISSING_LINE_421>>>
# <<<MISSING_LINE_422>>>
# <<<MISSING_LINE_423>>>
# <<<MISSING_LINE_424>>>
# <<<MISSING_LINE_425>>>
# <<<MISSING_LINE_426>>>
# <<<MISSING_LINE_427>>>
# <<<MISSING_LINE_428>>>
# <<<MISSING_LINE_429>>>
# <<<MISSING_LINE_430>>>
# <<<MISSING_LINE_431>>>
# <<<MISSING_LINE_432>>>
# <<<MISSING_LINE_433>>>
# <<<MISSING_LINE_434>>>
# <<<MISSING_LINE_435>>>
# <<<MISSING_LINE_436>>>
# <<<MISSING_LINE_437>>>
# <<<MISSING_LINE_438>>>
# <<<MISSING_LINE_439>>>
# <<<MISSING_LINE_440>>>
# <<<MISSING_LINE_441>>>
# <<<MISSING_LINE_442>>>
# <<<MISSING_LINE_443>>>
# <<<MISSING_LINE_444>>>
# <<<MISSING_LINE_445>>>
# <<<MISSING_LINE_446>>>
# <<<MISSING_LINE_447>>>
# <<<MISSING_LINE_448>>>
# <<<MISSING_LINE_449>>>
# <<<MISSING_LINE_450>>>
# <<<MISSING_LINE_451>>>
# <<<MISSING_LINE_452>>>
# <<<MISSING_LINE_453>>>
# <<<MISSING_LINE_454>>>
# <<<MISSING_LINE_455>>>
# <<<MISSING_LINE_456>>>
# <<<MISSING_LINE_457>>>
# <<<MISSING_LINE_458>>>
# <<<MISSING_LINE_459>>>
def para(text: str, style: str = "body") -> Paragraph:
# <<<MISSING_LINE_461>>>
# <<<MISSING_LINE_462>>>
# <<<MISSING_LINE_463>>>
def heading(text: str, level: int = 1) -> Paragraph:
# <<<MISSING_LINE_465>>>
# <<<MISSING_LINE_466>>>
# <<<MISSING_LINE_467>>>
def bullet(text: str) -> Paragraph:
# <<<MISSING_LINE_469>>>
# <<<MISSING_LINE_470>>>
# <<<MISSING_LINE_471>>>
def numbered(index: int, text: str) -> Paragraph:
# <<<MISSING_LINE_473>>>
# <<<MISSING_LINE_474>>>
# <<<MISSING_LINE_475>>>
def callout(title: str, body: str, fill=LIGHT, accent=BLUE, *, dark: bool = False) -> Table:
# <<<MISSING_LINE_477>>>
# <<<MISSING_LINE_478>>>
# <<<MISSING_LINE_479>>>
# <<<MISSING_LINE_480>>>
# <<<MISSING_LINE_481>>>
# <<<MISSING_LINE_482>>>
# <<<MISSING_LINE_483>>>
# <<<MISSING_LINE_484>>>
# <<<MISSING_LINE_485>>>
# <<<MISSING_LINE_486>>>
# <<<MISSING_LINE_487>>>
# <<<MISSING_LINE_488>>>
# <<<MISSING_LINE_489>>>
# <<<MISSING_LINE_490>>>
# <<<MISSING_LINE_491>>>
# <<<MISSING_LINE_492>>>
# <<<MISSING_LINE_493>>>
# <<<MISSING_LINE_494>>>
# <<<MISSING_LINE_495>>>
# <<<MISSING_LINE_496>>>
# <<<MISSING_LINE_497>>>
# <<<MISSING_LINE_498>>>
# <<<MISSING_LINE_499>>>
# <<<MISSING_LINE_500>>>
# <<<MISSING_LINE_501>>>
# <<<MISSING_LINE_502>>>
# <<<MISSING_LINE_503>>>
# <<<MISSING_LINE_504>>>
# <<<MISSING_LINE_505>>>
# <<<MISSING_LINE_506>>>
# <<<MISSING_LINE_507>>>
def table(
# <<<MISSING_LINE_509>>>
# <<<MISSING_LINE_510>>>
# <<<MISSING_LINE_511>>>
# <<<MISSING_LINE_512>>>
# <<<MISSING_LINE_513>>>
# <<<MISSING_LINE_514>>>
# <<<MISSING_LINE_515>>>
# <<<MISSING_LINE_516>>>
# <<<MISSING_LINE_517>>>
# <<<MISSING_LINE_518>>>
# <<<MISSING_LINE_519>>>
# <<<MISSING_LINE_520>>>
# <<<MISSING_LINE_521>>>
# <<<MISSING_LINE_522>>>
# <<<MISSING_LINE_523>>>
# <<<MISSING_LINE_524>>>
# <<<MISSING_LINE_525>>>
# <<<MISSING_LINE_526>>>
# <<<MISSING_LINE_527>>>
# <<<MISSING_LINE_528>>>
# <<<MISSING_LINE_529>>>
# <<<MISSING_LINE_530>>>
# <<<MISSING_LINE_531>>>
# <<<MISSING_LINE_532>>>
# <<<MISSING_LINE_533>>>
# <<<MISSING_LINE_534>>>
# <<<MISSING_LINE_535>>>
# <<<MISSING_LINE_536>>>
# <<<MISSING_LINE_537>>>
# <<<MISSING_LINE_538>>>
# <<<MISSING_LINE_539>>>
def code_block(text: str) -> Table:
# <<<MISSING_LINE_541>>>
# <<<MISSING_LINE_542>>>
# <<<MISSING_LINE_543>>>
# <<<MISSING_LINE_544>>>
# <<<MISSING_LINE_545>>>
# <<<MISSING_LINE_546>>>
# <<<MISSING_LINE_547>>>
# <<<MISSING_LINE_548>>>
# <<<MISSING_LINE_549>>>
# <<<MISSING_LINE_550>>>
# <<<MISSING_LINE_551>>>
# <<<MISSING_LINE_552>>>
# <<<MISSING_LINE_553>>>
def caption(text: str) -> Paragraph:
# <<<MISSING_LINE_555>>>
# <<<MISSING_LINE_556>>>
# <<<MISSING_LINE_557>>>
def chapter(story: list, number: int | str, title: str, summary: str) -> None:
# <<<MISSING_LINE_559>>>
# <<<MISSING_LINE_560>>>
# <<<MISSING_LINE_561>>>
# <<<MISSING_LINE_562>>>
def arrow(d: Drawing, x1: float, y1: float, x2: float, y2: float, color=BLUE, width=1.5) -> None:
# <<<MISSING_LINE_564>>>
# <<<MISSING_LINE_565>>>
# <<<MISSING_LINE_566>>>
# <<<MISSING_LINE_567>>>
# <<<MISSING_LINE_568>>>
# <<<MISSING_LINE_569>>>
# <<<MISSING_LINE_570>>>
# <<<MISSING_LINE_571>>>
# <<<MISSING_LINE_572>>>
# <<<MISSING_LINE_573>>>
# <<<MISSING_LINE_574>>>
# <<<MISSING_LINE_575>>>
def node(d: Drawing, x: float, y: float, w: float, h: float, text: str, fill=LIGHT, stroke=BLUE, size=8) -> None:
# <<<MISSING_LINE_577>>>
# <<<MISSING_LINE_578>>>
# <<<MISSING_LINE_579>>>
# <<<MISSING_LINE_580>>>
# <<<MISSING_LINE_581>>>
# <<<MISSING_LINE_582>>>
# <<<MISSING_LINE_583>>>
# <<<MISSING_LINE_584>>>
# <<<MISSING_LINE_585>>>
# <<<MISSING_LINE_586>>>
# <<<MISSING_LINE_587>>>
# <<<MISSING_LINE_588>>>
# <<<MISSING_LINE_589>>>
# <<<MISSING_LINE_590>>>
# <<<MISSING_LINE_591>>>
def architecture_diagram() -> Drawing:
# <<<MISSING_LINE_593>>>
# <<<MISSING_LINE_594>>>
# <<<MISSING_LINE_595>>>
# <<<MISSING_LINE_596>>>
# <<<MISSING_LINE_597>>>
# <<<MISSING_LINE_598>>>
# <<<MISSING_LINE_599>>>
# <<<MISSING_LINE_600>>>
# <<<MISSING_LINE_601>>>
# <<<MISSING_LINE_602>>>
# <<<MISSING_LINE_603>>>
# <<<MISSING_LINE_604>>>
# <<<MISSING_LINE_605>>>
# <<<MISSING_LINE_606>>>
# <<<MISSING_LINE_607>>>
# <<<MISSING_LINE_608>>>
# <<<MISSING_LINE_609>>>
# <<<MISSING_LINE_610>>>
# <<<MISSING_LINE_611>>>
# <<<MISSING_LINE_612>>>
    node(d, 226, 54, 120, 52, "Procedural intelligence\ncompiler boundary", MINT, TEAL, 7.3)
# <<<MISSING_LINE_614>>>
# <<<MISSING_LINE_615>>>
# <<<MISSING_LINE_616>>>
# <<<MISSING_LINE_617>>>
# <<<MISSING_LINE_618>>>
# <<<MISSING_LINE_619>>>
# <<<MISSING_LINE_620>>>
# <<<MISSING_LINE_621>>>
# <<<MISSING_LINE_622>>>
# <<<MISSING_LINE_623>>>
# <<<MISSING_LINE_624>>>
# <<<MISSING_LINE_625>>>
# <<<MISSING_LINE_626>>>
# <<<MISSING_LINE_627>>>
# <<<MISSING_LINE_628>>>
# <<<MISSING_LINE_629>>>
# <<<MISSING_LINE_630>>>
# <<<MISSING_LINE_631>>>
def compiler_diagram() -> Drawing:
# <<<MISSING_LINE_633>>>
# <<<MISSING_LINE_634>>>
# <<<MISSING_LINE_635>>>
# <<<MISSING_LINE_636>>>
# <<<MISSING_LINE_637>>>
# <<<MISSING_LINE_638>>>
# <<<MISSING_LINE_639>>>
# <<<MISSING_LINE_640>>>
# <<<MISSING_LINE_641>>>
# <<<MISSING_LINE_642>>>
# <<<MISSING_LINE_643>>>
# <<<MISSING_LINE_644>>>
# <<<MISSING_LINE_645>>>
# <<<MISSING_LINE_646>>>
# <<<MISSING_LINE_647>>>
# <<<MISSING_LINE_648>>>
# <<<MISSING_LINE_649>>>
# <<<MISSING_LINE_650>>>
# <<<MISSING_LINE_651>>>
# <<<MISSING_LINE_652>>>
# <<<MISSING_LINE_653>>>
# <<<MISSING_LINE_654>>>
# <<<MISSING_LINE_655>>>
# <<<MISSING_LINE_656>>>
# <<<MISSING_LINE_657>>>
# <<<MISSING_LINE_658>>>
# <<<MISSING_LINE_659>>>
# <<<MISSING_LINE_660>>>
# <<<MISSING_LINE_661>>>
# <<<MISSING_LINE_662>>>
# <<<MISSING_LINE_663>>>
# <<<MISSING_LINE_664>>>
# <<<MISSING_LINE_665>>>
# <<<MISSING_LINE_666>>>
# <<<MISSING_LINE_667>>>
# <<<MISSING_LINE_668>>>
# <<<MISSING_LINE_669>>>
# <<<MISSING_LINE_670>>>
def experience_loop_diagram() -> Drawing:
# <<<MISSING_LINE_672>>>
# <<<MISSING_LINE_673>>>
# <<<MISSING_LINE_674>>>
# <<<MISSING_LINE_675>>>
# <<<MISSING_LINE_676>>>
# <<<MISSING_LINE_677>>>
# <<<MISSING_LINE_678>>>
# <<<MISSING_LINE_679>>>
# <<<MISSING_LINE_680>>>
# <<<MISSING_LINE_681>>>
# <<<MISSING_LINE_682>>>
# <<<MISSING_LINE_683>>>
# <<<MISSING_LINE_684>>>
# <<<MISSING_LINE_685>>>
# <<<MISSING_LINE_686>>>
# <<<MISSING_LINE_687>>>
# <<<MISSING_LINE_688>>>
# <<<MISSING_LINE_689>>>
# <<<MISSING_LINE_690>>>
# <<<MISSING_LINE_691>>>
# <<<MISSING_LINE_692>>>
# <<<MISSING_LINE_693>>>
# <<<MISSING_LINE_694>>>
# <<<MISSING_LINE_695>>>
def trust_diagram() -> Drawing:
# <<<MISSING_LINE_697>>>
# <<<MISSING_LINE_698>>>
# <<<MISSING_LINE_699>>>
# <<<MISSING_LINE_700>>>
# <<<MISSING_LINE_701>>>
# <<<MISSING_LINE_702>>>
# <<<MISSING_LINE_703>>>
# <<<MISSING_LINE_704>>>
# <<<MISSING_LINE_705>>>
# <<<MISSING_LINE_706>>>
# <<<MISSING_LINE_707>>>
# <<<MISSING_LINE_708>>>
# <<<MISSING_LINE_709>>>
# <<<MISSING_LINE_710>>>
def repository_graph_diagram() -> Drawing:
# <<<MISSING_LINE_712>>>
# <<<MISSING_LINE_713>>>
# <<<MISSING_LINE_714>>>
# <<<MISSING_LINE_715>>>
# <<<MISSING_LINE_716>>>
# <<<MISSING_LINE_717>>>
# <<<MISSING_LINE_718>>>
# <<<MISSING_LINE_719>>>
# <<<MISSING_LINE_720>>>
# <<<MISSING_LINE_721>>>
# <<<MISSING_LINE_722>>>
# <<<MISSING_LINE_723>>>
# <<<MISSING_LINE_724>>>
# <<<MISSING_LINE_725>>>
# <<<MISSING_LINE_726>>>
# <<<MISSING_LINE_727>>>
# <<<MISSING_LINE_728>>>
# <<<MISSING_LINE_729>>>
# <<<MISSING_LINE_730>>>
# <<<MISSING_LINE_731>>>
# <<<MISSING_LINE_732>>>
# <<<MISSING_LINE_733>>>
# <<<MISSING_LINE_734>>>
# <<<MISSING_LINE_735>>>
# <<<MISSING_LINE_736>>>
# <<<MISSING_LINE_737>>>
# <<<MISSING_LINE_738>>>
# <<<MISSING_LINE_739>>>
# <<<MISSING_LINE_740>>>
# <<<MISSING_LINE_741>>>
# <<<MISSING_LINE_742>>>
# <<<MISSING_LINE_743>>>
# <<<MISSING_LINE_744>>>
def timeline_diagram() -> Drawing:
# <<<MISSING_LINE_746>>>
# <<<MISSING_LINE_747>>>
# <<<MISSING_LINE_748>>>
# <<<MISSING_LINE_749>>>
# <<<MISSING_LINE_750>>>
# <<<MISSING_LINE_751>>>
# <<<MISSING_LINE_752>>>
# <<<MISSING_LINE_753>>>
# <<<MISSING_LINE_754>>>
# <<<MISSING_LINE_755>>>
# <<<MISSING_LINE_756>>>
# <<<MISSING_LINE_757>>>
# <<<MISSING_LINE_758>>>
# <<<MISSING_LINE_759>>>
# <<<MISSING_LINE_760>>>
# <<<MISSING_LINE_761>>>
# <<<MISSING_LINE_762>>>
# <<<MISSING_LINE_763>>>
# <<<MISSING_LINE_764>>>
# <<<MISSING_LINE_765>>>
# <<<MISSING_LINE_766>>>
# <<<MISSING_LINE_767>>>
def build_charts() -> dict[str, Path]:
# <<<MISSING_LINE_769>>>
# <<<MISSING_LINE_770>>>
# <<<MISSING_LINE_771>>>
# <<<MISSING_LINE_772>>>
# <<<MISSING_LINE_773>>>
# <<<MISSING_LINE_774>>>
# <<<MISSING_LINE_775>>>
# <<<MISSING_LINE_776>>>
# <<<MISSING_LINE_777>>>
# <<<MISSING_LINE_778>>>
# <<<MISSING_LINE_779>>>
# <<<MISSING_LINE_780>>>
# <<<MISSING_LINE_781>>>
# <<<MISSING_LINE_782>>>
# <<<MISSING_LINE_783>>>
# <<<MISSING_LINE_784>>>
# <<<MISSING_LINE_785>>>
# <<<MISSING_LINE_786>>>
# <<<MISSING_LINE_787>>>
# <<<MISSING_LINE_788>>>
# <<<MISSING_LINE_789>>>
# <<<MISSING_LINE_790>>>
# <<<MISSING_LINE_791>>>
# <<<MISSING_LINE_792>>>
# <<<MISSING_LINE_793>>>
# <<<MISSING_LINE_794>>>
# <<<MISSING_LINE_795>>>
# <<<MISSING_LINE_796>>>
# <<<MISSING_LINE_797>>>
# <<<MISSING_LINE_798>>>
# <<<MISSING_LINE_799>>>
# <<<MISSING_LINE_800>>>
# <<<MISSING_LINE_801>>>
# <<<MISSING_LINE_802>>>
# <<<MISSING_LINE_803>>>
# <<<MISSING_LINE_804>>>
# <<<MISSING_LINE_805>>>
# <<<MISSING_LINE_806>>>
# <<<MISSING_LINE_807>>>
# <<<MISSING_LINE_808>>>
# <<<MISSING_LINE_809>>>
# <<<MISSING_LINE_810>>>
# <<<MISSING_LINE_811>>>
    p = TMP / "benchmark_coverage.png"
# <<<MISSING_LINE_813>>>
# <<<MISSING_LINE_814>>>
# <<<MISSING_LINE_815>>>
# <<<MISSING_LINE_816>>>
# <<<MISSING_LINE_817>>>
# <<<MISSING_LINE_818>>>
# <<<MISSING_LINE_819>>>
# <<<MISSING_LINE_820>>>
# <<<MISSING_LINE_821>>>
# <<<MISSING_LINE_822>>>
# <<<MISSING_LINE_823>>>
    ax.plot(projects, deprecated, marker="o", linewidth=1.8, color="#A23B3B", label="deprecated")
# <<<MISSING_LINE_825>>>
# <<<MISSING_LINE_826>>>
# <<<MISSING_LINE_827>>>
# <<<MISSING_LINE_828>>>
# <<<MISSING_LINE_829>>>
# <<<MISSING_LINE_830>>>
# <<<MISSING_LINE_831>>>
# <<<MISSING_LINE_832>>>
# <<<MISSING_LINE_833>>>
# <<<MISSING_LINE_834>>>
# <<<MISSING_LINE_835>>>
# <<<MISSING_LINE_836>>>
# <<<MISSING_LINE_837>>>
# <<<MISSING_LINE_838>>>
# <<<MISSING_LINE_839>>>
# <<<MISSING_LINE_840>>>
# <<<MISSING_LINE_841>>>
# <<<MISSING_LINE_842>>>
# <<<MISSING_LINE_843>>>
# <<<MISSING_LINE_844>>>
# <<<MISSING_LINE_845>>>
# <<<MISSING_LINE_846>>>
# <<<MISSING_LINE_847>>>
# <<<MISSING_LINE_848>>>
# <<<MISSING_LINE_849>>>
# <<<MISSING_LINE_850>>>
# <<<MISSING_LINE_851>>>
# <<<MISSING_LINE_852>>>
# <<<MISSING_LINE_853>>>
# <<<MISSING_LINE_854>>>
# <<<MISSING_LINE_855>>>
# <<<MISSING_LINE_856>>>
# <<<MISSING_LINE_857>>>
# <<<MISSING_LINE_858>>>
# <<<MISSING_LINE_859>>>
# <<<MISSING_LINE_860>>>
# <<<MISSING_LINE_861>>>
# <<<MISSING_LINE_862>>>
def add_figure(story: list, obj, label: str) -> None:
# <<<MISSING_LINE_864>>>
# <<<MISSING_LINE_865>>>
# <<<MISSING_LINE_866>>>
# <<<MISSING_LINE_867>>>
# <<<MISSING_LINE_868>>>
# <<<MISSING_LINE_869>>>
# <<<MISSING_LINE_870>>>
# <<<MISSING_LINE_871>>>
# <<<MISSING_LINE_872>>>
# <<<MISSING_LINE_873>>>
# <<<MISSING_LINE_874>>>
def add_component(
# <<<MISSING_LINE_876>>>
# <<<MISSING_LINE_877>>>
# <<<MISSING_LINE_878>>>
# <<<MISSING_LINE_879>>>
# <<<MISSING_LINE_880>>>
# <<<MISSING_LINE_881>>>
# <<<MISSING_LINE_882>>>
# <<<MISSING_LINE_883>>>
# <<<MISSING_LINE_884>>>
# <<<MISSING_LINE_885>>>
# <<<MISSING_LINE_886>>>
# <<<MISSING_LINE_887>>>
# <<<MISSING_LINE_888>>>
# <<<MISSING_LINE_889>>>
# <<<MISSING_LINE_890>>>
# <<<MISSING_LINE_891>>>
# <<<MISSING_LINE_892>>>
# <<<MISSING_LINE_893>>>
# <<<MISSING_LINE_894>>>
# <<<MISSING_LINE_895>>>
# <<<MISSING_LINE_896>>>
# <<<MISSING_LINE_897>>>
# <<<MISSING_LINE_898>>>
    story.append(table(rows, [1.25 * inch, CONTENT_W - 1.25 * inch]))
    story.append(Spacer(1, 7))


def build_story() -> list:
    s: list = []

    # Cover
    s.extend([
        Spacer(1, 1.15 * inch),
        para("ASTRA RESEARCH MONOGRAPH", "chapter_number"),
        Paragraph("Astra Next", ST["cover_title"]),
        Paragraph(
            "A Deterministic, Self-Improving Software Engineering System",
            ST["cover_title"],
        ),
        Paragraph(
            "Research vision, Phase 1 charter integration, procedural intelligence compiler, safety model, and experimental roadmap",
            ST["cover_subtitle"],
        ),
        Spacer(1, 0.45 * inch),
        callout(
            "Defining thesis",
            "<b>Astra compiles verified procedural capabilities from experience.</b><br/>"
            "The model is temporary reasoning support. The acquired procedural intelligence belongs to Astra.",
            fill=HexColor("#123A55"),
            accent=GOLD,
            dark=True,
        ),
        Spacer(1, 1.05 * inch),
        para(
            "<font color='#D6E4EE'><b>Research blueprint v1.1 - Phase 1 normative revision</b><br/>"
            "Repository evidence snapshot: branch feature/chat-native-approval, commit 9d7b63a41cf4<br/>"
            "Prepared 29 July 2026 - local-first, Python-first, hardware-constrained research programme<br/>"
            "The Phase 1 charter is normative and overrides less precise language in this monograph.</font>",
            "body_left",
        ),
        PageBreak(),
    ])

    # Front matter
    s.append(heading("Document status and reading guide", 1))
    s.append(para(
        "This monograph is both an architecture specification and a research proposal. It reconstructs the "
        "reasoning that led from the original Astra coding-assistant concept to the refined idea of verified "
        "procedural capability compilation. It is intentionally explicit about evidence status. Statements "
        "marked <b>implemented</b> are grounded in the inspected repository. <b>Emerging</b> elements exist in "
        "partial or experimental form. <b>Proposed</b> elements are architectural designs. <b>Research</b> "
        "elements are hypotheses that require controlled experiments."
    ))
    legend = [[
        StatusPill("implemented", GREEN),
        StatusPill("emerging", GOLD),
        StatusPill("proposed", BLUE),
        StatusPill("research", PURPLE),
    ]]
    lt = Table(legend, colWidths=[CONTENT_W / 4] * 4)
    lt.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    s.append(lt)
    s.append(Spacer(1, 10))
    s.append(callout(
        "Claim discipline",
        "Astra is not presented as the first system to extract skills, learn workflows, or reuse trajectories. "
        "Related work already explores those topics. The proposed contribution is the continuous compilation "
        "of real software-engineering outcomes into deterministic, executable, model-independent procedures "
        "that remain subordinate to a fixed safety kernel.",
        fill=PALE_GOLD,
        accent=GOLD,
    ))
    s.append(callout(
        "Normative precedence",
        "This blueprint explains Astra's vision, architecture, evidence snapshot, and research programme. The "
        "Phase 1 charter freezes definitions and epistemic rules. Where the two differ, the charter controls. "
        "Changes to capability identity, experience semantics, attribution, preference governance, learning "
        "authority, or transfer evaluation require an explicit charter amendment.",
        fill=MINT,
        accent=TEAL,
    ))
    s.append(Spacer(1, 10))
    s.append(heading("Executive abstract", 2))
    s.append(para(
        "Astra began as an attempt to create a useful local coding assistant on a laptop with an NVIDIA RTX "
        "3050 Laptop GPU and 4 GiB of VRAM. The hardware can run small coding models, but it cannot reproduce "
        "the broad internal knowledge and inference capacity of frontier-scale language models. Live work with "
        "Qwen2.5-Coder 1.5B made the limitation concrete: the model could write a requested function, yet whole-"
        "file rewriting could omit correct existing functions. A deterministic append operation succeeded "
        "because the system reduced the model's responsibility to the fragment it handled well."
    ))
    s.append(para(
        "That lesson changes the research question. Astra should not ask a small model to imitate a large model. "
        "It should build intelligence in software: repository graphs, typed intent, evidence packages, semantic "
        "operations, strict state machines, isolated validation, outcome ledgers, strategy models, and eventually "
        "a capability compiler. The compiler observes verified trajectories, abstracts repeated procedures into "
        "a restricted intermediate representation, tests them through simulation and replay, benchmarks them on "
        "held-out tasks, and recommends only qualified artifacts for governed promotion into a versioned capability "
        "library. The broader Procedural Intelligence Compiler also preserves applicability, invariants, verification "
        "contracts, attribution, failure evidence, and recomputable performance assessments."
    ))
    s.append(para(
        "The central falsifiable hypothesis is that Astra, after a chronological sequence of projects, should "
        "solve more held-out tasks with fewer model calls, retries, and clarifications than its initial version "
        "while using the same model, prompts, hardware, and safety kernel. If performance does not improve under "
        "those controls, the capability-compilation hypothesis is rejected or revised."
    ))
    s.append(PageBreak())
    s.append(heading("Phase 1 normative summary", 1))
    s.append(callout(
        "Frozen research identity",
        "<b>Astra is an evidence-governed procedural intelligence architecture for software engineering.</b> "
        "Its learned state may improve retrieval, ranking, applicability, clarification, failure prediction, and "
        "composite capabilities. It may not learn or rewrite authority, permissions, workspace boundaries, artifact "
        "integrity, approval, isolation, idempotency, or verification authority.",
        fill=MINT,
        accent=TEAL,
    ))
    s.append(heading("N.1 Normative definitions", 2))
    s.append(table([
        ["Term", "Frozen Phase 1 definition"],
        ["Software-engineering intelligence", "Measured improvement in held-out engineering decisions and outcomes under fixed resources, authority, and model support as verified experience accumulates."],
        ["Observation", "A provenance-preserved event or artifact. It is not automatically evidence."],
        ["Evidence", "An observation interpreted for or against an explicit claim through a versioned evidence vocabulary."],
        ["Experience", "An immutable episode joining pre-decision context, available features, alternatives, intervention, execution, verification, user observations, outcome, and attribution hypotheses."],
        ["Capability", "A versioned procedural artifact C=(A,P,I,V): applicability, procedure, invariants/safety argument, and verification contract."],
        ["Learning", "A controlled, evidence-backed change to derived decision state that may improve future outcomes without changing the fixed authority kernel."],
        ["Transfer", "Reuse of an unchanged capability on held-out context that differs along declared capability-relevant dimensions while P, I, and V remain valid."],
    ], [1.65 * inch, CONTENT_W - 1.65 * inch], small=True))
    s.append(heading("N.2 Capability identity", 2))
    s.append(para(
        "Two implementations belong to one capability only while they share one causal procedure family, one "
        "safety argument, and one verification contract. Source similarity, trajectory embeddings, or a failed "
        "unification search cannot decide identity. Applicability predicates may select and parameterise a "
        "procedure; they may not hide an alternative procedure."
    ))
    s.append(table([
        ["Evolution decision", "Meaning", "Required discipline"],
        ["Refine", "Narrow or sharpen A while preserving P, I, and V.", "Recompute inherited statistics from immutable episodes under the new predicate."],
        ["Split", "Create a probationary child for a distinct causal procedure or safety/verification account.", "Require a minimal distinguishing witness and independent transfer evidence."],
        ["Compose", "Build a composite from existing capabilities.", "Require child contracts plus an emergent integration verifier."],
    ], [1.0 * inch, 2.5 * inch, CONTENT_W - 3.5 * inch], small=True))
    s.append(PageBreak())
    s.append(heading("Phase 1 normative summary - evidence and governance", 1))
    s.append(heading("N.3 Epistemic rules", 2))
    for item in [
        "<b>Feedback is an observation. Attribution is a hypothesis.</b> Rejection does not prove plan defect, preference, convention mismatch, interaction cost, or presentation failure.",
        "<b>Failed bounded unification leaves identity unresolved.</b> It may justify a provisional split candidate, never an automatic permanent split.",
        "<b>Outcome history belongs to immutable experiences.</b> Capability statistics are derived and recomputed for the current capability and predicate versions.",
        "<b>One severe failure may veto promotion.</b> Invariant or safety violations have different evidential force from infrastructure failures or correct abstention.",
        "<b>User preferences have the lowest authority.</b> They are testable output constraints, quarantined from capability compilation, confirmed before first influence, visible, scoped, and deletable as derived views.",
        "<b>Held-out means uninvolved.</b> A target is not held out if it influenced synthesis, predicate refinement, operation vocabulary, verifier design, thresholds, or evolution decisions.",
    ]:
        s.append(bullet(item))
    s.append(heading("N.4 Recomputable assessments", 2))
    s.append(para(
        "Immutable experiences preserve the raw feature values and vocabulary versions required to evaluate future "
        "predicates and transfer profiles that did not yet exist. Capability statistics, applicability, preference "
        "views, and transfer strata are projections, not historical truth. Every derived assessment records its "
        "version, original classification, current recomputation, and supersession status."
    ))
    s.append(heading("N.5 Compiler authority boundary", 2))
    s.append(table([
        ["Compiler may", "Compiler may not"],
        ["Construct candidate DSL and applicability hypotheses.", "Write arbitrary Python into the trusted kernel."],
        ["Mine positive and negative experience.", "Treat public examples as trusted production procedures."],
        ["Recommend refine, split, compose, promotion, degradation, or revocation.", "Grant production authority or approve its own recommendation."],
        ["Request simulation, replay, and held-out evaluation through trusted services.", "Redefine a verifier after seeing held-out results."],
        ["Detect recurring ambiguity in the atomic-operation vocabulary.", "Silently change the operation vocabulary."],
    ], [CONTENT_W / 2, CONTENT_W / 2], small=True))
    s.append(callout(
        "Scientific test",
        "After 1,000 chronological projects, Astra must improve repository-disjoint transfer, correct abstention, "
        "efficiency, and calibration relative to static, memory-only, and ranking-only baselines. Memorising exact "
        "patches, increasing capability count, or narrowing coverage without reporting it is not intelligence.",
        fill=PALE_GOLD,
        accent=GOLD,
    ))
    s.append(PageBreak())
    s.append(Paragraph("Contents", ST["toc_header"]))
    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle(
            "TOC1", fontName=FONT_BOLD, fontSize=9.2, leading=14, leftIndent=0,
            firstLineIndent=0, textColor=NAVY, spaceBefore=4,
        ),
        ParagraphStyle(
            "TOC2", fontName=FONT, fontSize=8.0, leading=11, leftIndent=18,
            firstLineIndent=0, textColor=MUTED, spaceBefore=1,
        ),
    ]
    s.append(toc)

    # Chapter 1
    chapter(
        s, 1, "Why Astra Changed Direction",
        "The project did not abandon its original ambition. It changed the unit of intelligence from a monolithic model to a verified software system.",
    )
    add_figure(s, timeline_diagram(), "Figure 1.1 - The evolution of Astra's research question.")
    s.append(heading("1.1 The original constraint", 2))
    s.append(para(
        "The starting requirement was unusually strict: build a local coding assistant that feels responsive and "
        "competent on a laptop with approximately 32 GiB of installed physical RAM, two SSDs, and a 4 GiB RTX 3050 "
        "Laptop GPU. Everything "
        "should remain local. Long training, model downloads during operation, uncontrolled shell execution, and "
        "cloud dependence were unacceptable. The initial instinct was to combine a small language model with "
        "specialist deep-learning models, machine-learning classifiers, Python rules, workers, retrieval, and "
        "memory so that no single component carried the full cognitive load."
    ))
    s.append(para(
        "That instinct was directionally correct, but early architecture accumulated overlapping subsystems: "
        "legacy SLM routes beside canonical local-AI services, multiple orchestration paths, old and new retrieval "
        "systems, broad specialist concepts, and a growing API surface. The problem was not a lack of ideas. It "
        "was the absence of a sufficiently narrow definition of what each layer was allowed to decide."
    ))
    s.append(heading("1.2 Hardware is a design input, not an embarrassment", 2))
    s.append(table([
        ["Resource", "Available system", "Architectural consequence"],
        ["GPU", "RTX 3050 Laptop, 4 GiB VRAM", "One bounded local GPU workload; small quantized model; explicit admission."],
        ["Installed physical RAM", "33,962,164,224 bytes (31.63 GiB)", "Host capacity measured through Windows on 29 July 2026."],
        ["Windows-visible RAM", "33,962,164,224 bytes; 17,610,481,664 free", "Free memory is a time-varying observation, not a machine constant."],
        ["WSL-visible RAM", "Runtime-dependent; capture per experiment", "Never infer WSL limits from installed host RAM."],
        ["Storage", "1 TB + 512 GB SSD", "Immutable artifacts, benchmark fixtures, snapshots, and model files are feasible."],
        ["Runtime", "Python-first, local-only", "Prefer ASTs, rules, subprocess isolation, and small learned rankers."],
        ["Model episode", "Qwen2.5-Coder 1.5B via Ollama", "Decisive failure involved 1.5B; the architectural conclusion is model-independent."],
        ["Later observation", "Qwen2.5-Coder 3B, operator-reported", "Improved some structured generation; did not remove bounded responsibility or deterministic integration."],
    ], [1.2 * inch, 1.85 * inch, CONTENT_W - 3.05 * inch], small=True))
    s.append(heading("1.3 What live synthesis taught us", 2))
    s.append(para(
        "The decisive engineering episode was an apparently trivial calculator task. Whole-file synthesis asked "
        "the 1.5B model to preserve existing functions while adding another. The model implemented the new "
        "function correctly but dropped valid existing code. Deterministic semantic guards rejected the proposal. "
        "The successful redesign introduced a bounded append-python-symbol strategy: the model wrote only the new "
        "function, while Astra preserved and extended the existing file. The safety system did not become weaker; "
        "the proposal construction became easier for the model and easier to verify."
    ))
    s.append(callout(
        "General lesson",
        s.append(numbered(idx, f"<b>{title}.</b> {body}"))
    s.append(heading("3.2 Intelligence layers", 2))
    s.append(table([
        ["Layer", "What changes", "What must remain fixed"],
        ["Semantic", "Mappings from language to typed hypotheses; clarification policy.", "No direct authority over project state."],
        ["Repository intelligence", "Profiles, relevance scores, evidence contracts.", "Canonical file identities and path safety."],
        ["Decision", "Strategy rankings and failure probabilities.", "Allowlisted actions and transition guards."],
        ["Procedural", "Compiled capability artifacts and applicability models.", "Trusted atomic operation implementations."],
        ["Safety", "Nothing learned automatically.", "Scope, approval, isolation, integrity, verification."],
    ], [1.0 * inch, 2.55 * inch, CONTENT_W - 3.55 * inch]))
    s.append(heading("3.3 Why a fixed safety kernel matters", 2))
    add_figure(s, trust_diagram(), "Figure 3.1 - Learned intelligence is contained inside a fixed authority boundary.")
        "validator feedback. It is not the project lifecycle authority, the verifier, the memory database, or the "
        "planner of unrestricted actions. Replacing Qwen with another local model should change generation quality, "
        "not erase Astra's accumulated capabilities."
    ))

    # Chapter 4
    chapter(
        s, 4, "Current Astra: Evidence and Baseline",
        "Astra already contains a strong canonical control plane, local-AI boundary, worker, isolation layer, retrieval, deterministic analysis, and an emerging decision/outcome spine.",
    )
    s.append(heading("4.1 Repository snapshot", 2))
    s.append(table([
        ["Evidence item", "Observed value", "Interpretation"],
        ["Branch", "feature/chat-native-approval", "Active local development branch."],
        ["Commit", "9d7b63a41cf4", "Reliable add-function synthesis and self-correcting retry."],
        ["Worktree state", "Dirty: 102 porcelain entries", "Counts include uncommitted local work; they are not a clean-commit measurement."],
        ["Test files", "190 test_*.py; 191 total Python files", "Measured under tests/ on 29 July 2026."],
        ["Backend test functions", "1,508", "Substantial safety and behavior coverage."],
        ["Deterministic benchmark", "40/40 passed; phase0.v1", "Artifact generated 28 July 2026 at 06:37:48 UTC under Python 3.12.3."],
        ["Canonical model boundary", "LocalAIService", "Admission, provider execution, provenance, and structured output."],
        ["Lifecycle authority", "ProjectControlPlane", "Exact transitions, approvals, attempts, artifacts, and idempotency."],
    ], [1.45 * inch, 1.85 * inch, CONTENT_W - 3.3 * inch], small=True))
    s.append(callout(
        "Snapshot commands",
        "<font name='AstraMono'>git status --porcelain=v1</font>; "
        "<font name='AstraMono'>rg --files tests -g 'test_*.py'</font>; "
        "<font name='AstraMono'>rg -n '^\\s*(async\\s+)?def\\s+test_' tests -g '*.py'</font>. "
        "Benchmark evidence comes from <font name='AstraMono'>benchmarks/results/baseline_2026-07-28.json</font>. "
        "Skipped-test counts are not implied because the pytest suite was not executed for this document revision.",
        fill=PALE_GOLD,
        accent=GOLD,
    ))
    add_figure(s, CHARTS["module_sizes"], "Figure 4.1 - Large files reveal consolidation work that should precede broad expansion.")
    s.append(heading("4.2 Implemented strengths", 2))
    strengths = [
        "Exact project, conversation, actor, revision, manifest, state-version, and idempotency bindings.",
        "Immutable plan, scope, approval, execution-attempt, event, and verifier evidence records.",
        "Durable worker ownership separated from browser and request processing.",
        "Fail-closed Docker execution with network disabled, non-root identity, pinned image, and bounded resources.",
        "Workspace registration and path allowlisting instead of arbitrary browser filesystem access.",
        "LocalAIService as canonical admission and generation authority.",
        "Strict provider readiness, model availability, GPU exclusivity, and typed local-AI failures.",
        "AST analysis, deterministic fixes, scaffolding blueprints, project retrieval, and focused benchmark fixtures.",
    ]
    for item in strengths:
        s.append(bullet(item))
    s.append(heading("4.3 Emerging research spine", 2))
    s.append(para(
        "The current worktree contains an advisory DecisionLayer, an append-only DecisionOutcomeStore, rules-first "
        "playbook selection, retrieval integration, and conservative memory-based strategy choice. The ranker is "
        "explicitly shadow-only. These are valuable beginnings, but they are not yet a capability compiler. They "
        "should be treated as instrumentation and baseline policy rather than proof of continual learning."
    ))
    s.append(heading("4.4 Complexity debt", 2))
    s.append(para(
    # Chapter 3
        "lines, coordinator execution approximately 1,466 lines, and the main React component approximately 2,262 "
        "lines. Legacy and canonical subsystems coexist. A senior engineering plan should consolidate to one model "
        "boundary, one project workflow, one retrieval system, one worker, and a smaller composition root before "
        "the research layer becomes production-critical."
    ))
    s.append(callout(
        "Historical-document caution",
        "Some repository documents describe earlier gaps that have since been closed. For example, the task-family "
        "specification originally described add_function_to_module as a 0% baseline gap, while the latest inspected "
        "phase0.v1 result reports all three cases passing. The monograph uses timestamped evidence rather than "
        "assuming every historical document describes the current code.",
        fill=PALE_GOLD,
        accent=GOLD,
    ))
    add_figure(s, CHARTS["benchmark"], "Figure 4.2 - Current deterministic benchmark composition and latest result.")

    # Chapter 5
    chapter(
        s, 5, "Target System Architecture",
        "Astra Next is a layered neuro-symbolic software-engineering system: deterministic structure and authority, learned decision support, and narrowly bounded local synthesis.",
    )
    add_figure(s, architecture_diagram(), "Figure 5.1 - Full target architecture and authority flow.")
    s.append(heading("5.1 End-to-end request flow", 2))
    flow = [
        "Conversation is translated into one or more typed intent hypotheses.",
        "Repository intelligence constructs a task-specific evidence package.",
        "The deterministic plan compiler maps the intent to bounded work units.",
        "The decision engine ranks allowlisted capabilities and estimates uncertainty.",
        "A trusted capability executes deterministic operations or requests a tiny model-generated fragment.",
        "Scope, schema, AST, semantic, integrity, and policy guards validate the proposal.",
        "Focused tests run automatically only in an allowlisted isolated validation profile.",
        "The user receives the plan, patch, retry history, and evidence; patch application remains explicitly approved.",
        "Post-apply verification generates immutable evidence and a canonical outcome.",
        "The experience engine records the decision context and outcome for later compilation.",
    ]
    for i, item in enumerate(flow, 1):
        s.append(numbered(i, item))
    s.append(heading("5.2 One canonical owner per responsibility", 2))
    s.append(table([
        ["Responsibility", "Canonical owner", "Forbidden duplication"],
        ["Lifecycle", "ProjectControlPlane", "Route-local or frontend state transitions."],
        ["Model execution", "LocalAIService", "Direct provider calls from feature modules."],
        ["Project coordination", "Canonical coordinator + worker", "Second autonomous job authority."],
        ["Retrieval", "Project-bound retrieval service", "Unbound generic RAG in production path."],
        ["Capabilities", "Versioned capability registry", "Prompt-only hidden procedures."],
        ["Verification", "Deterministic verifier", "Model claims treated as evidence."],
        ["Experience", "Append-only outcome ledger", "Mutable chat summaries as truth."],
    # Chapter 4
    s.append(heading("5.3 Deployment topology", 2))
    s.append(para(
        "Local development uses four explicit processes: Ollama, FastAPI, the continuous project worker, and the "
        "frontend. The backend and worker consume the same reviewed environment configuration. Astra does not "
        "start Ollama, pull models, install packages, or build execution images during a project request. GPU "
        "scheduling remains exclusive unless a stricter mechanism is introduced."
    ))
    s.append(code_block("""
Operator starts Ollama
        |
        +-- FastAPI backend  ------ SQLite canonical state
        ["Backend test functions", "1,508", "Substantial safety and behavior coverage."],
        +-- Continuous worker ---- coordinator intents / bounded execution
        |
        +-- React frontend  ------- canonical read model and approvals

LocalAIService -> provider readiness -> GPU admission -> one model workload
Project worker -> disposable Docker snapshot -> focused validation
"""))
    s.append(heading("5.4 Why not a swarm of agents?", 2))
    s.append(para(
        "Layer cooperation should use typed contracts rather than conversational agents. Retrieval returns file "
        "identities and evidence artifacts. Planning returns work units. Validation returns typed results. A small "
        "model may be called inside a bounded stage, but inter-layer messages remain inspectable data. This design "
        "reduces token use, prevents circular conversation, and makes replay possible."
    ))

    # Chapter 6 - components
    chapter(
        s, 6, "Component Specifications",
        "Every layer is defined by its decision, inputs, outputs, algorithm, failure behavior, and benchmark. Vague component names are not enough.",
    )
    components = [
        (
            "Semantic Conversation Layer", "proposed",
            "Presents a Codex-like conversational experience while translating natural language into bounded intent hypotheses and explaining canonical state.",
            "User message, conversation binding, selected project, active project state, known vocabulary.",
            "Typed intent candidates, missing-slot questions, safe narration, no lifecycle mutation.",
            "Small SLM extraction plus deterministic schema validation, repository-aware vocabulary, and clarification thresholds.",
            "Ambiguity, invalid schema, or unavailable model produces clarification or deterministic fallback; never an invented action.",
            "Intent accuracy, clarification precision, user correction rate, latency, model-call count.",
        ),
        (
            "Intent Catalog and Parser", "emerging",
            "Defines the finite set of tasks Astra understands and maps messages into catalog entries with typed slots.",
            "Normalized text, repository context, existing conversation decisions.",
            "IntentResolution with task family, slots, confidence, alternatives, and clarification reason.",
            "Rules-first parsing, optional semantic model, exact slot validators, and catalog versioning.",
            "Unknown or conflicting requests resolve to clarification_needed rather than generic project execution.",
            "Macro/micro F1, unknown rejection accuracy, slot exact match, calibration.",
        ),
        (
            "Repository Intelligence Engine", "implemented",
            "Builds deterministic structural knowledge before any model reasons about code.",
            "Allowlisted root, manifests, ASTs, imports, configuration, test discovery, diagnostics.",
            "Repository profile, symbol graph, dependency edges, framework facts, evidence references.",
            "Bounded scanners, AST parsing, configuration readers, graph construction, content hashes.",
            "Limits, unsafe paths, symlinks, incomplete manifests, or parsing uncertainty remain explicit.",
            "Localization recall, graph accuracy, scan latency, manifest completeness, false positives.",
        ),
        (
            "Evidence Builder", "implemented",
            "Compresses repository structure into the minimum task-relevant evidence package.",
            "Typed intent, repository graph, retrieval candidates, file identities, existing tests.",
            "Versioned evidence artifact with approved paths, source excerpts, symbols, hashes, and provenance.",
            "Deterministic filters first, BM25/symbol ranking next, optional learned reranking last.",
    # Chapter 5
            "Evidence recall@k, token/character budget, stale-evidence rate, downstream success.",
        ),
        (
            "Deterministic Plan Compiler", "proposed",
            "Turns typed intent and evidence into bounded work units rather than asking a model to invent a workflow.",
            "Intent schema, evidence artifact, capability registry, project policy.",
            "Immutable plan revision with work units, scope, expected artifacts, and validation recipe.",
            "Rule-based mapping from task family to capability graph; unresolved choices become clarification.",
            "No matching safe capability yields capability_gap, not free-form autonomy.",
        "The deterministic plan compiler maps the intent to bounded work units.",
        ),
        (
            "Decision Engine and Strategy Ranker", "emerging",
            "Ranks allowlisted strategies while preserving deterministic default behavior and explicit uncertainty.",
            "Task family, repository profile, evidence quality, outcome summaries, resource state.",
            "Ranked strategies, predicted success, expected cost, explanation, clarification recommendation.",
            "Rules baseline; memory summary; later logistic regression, boosted trees, or contextual bandit in shadow mode.",
            "Ranker cannot introduce a new strategy or bypass gates; low confidence falls back to rules or clarification.",
            "Top-1 success, regret, calibration error, unsafe-selection rate, latency.",
        ),
        (
            "Capability Registry", "proposed",
            "Stores atomic and compiled procedures with versions, provenance, applicability, validators, and lifecycle state.",
            "Manually trusted operations and promoted compiled capability artifacts.",
            "Deterministic lookup by task, language, framework, evidence contract, and policy.",
            "Content-addressed declarative packages compiled to trusted Python operation handlers.",
            "Unknown DSL version, missing validator, revoked dependency, or ambiguous match fails closed.",
            "Coverage, reuse, overlap, activation precision, dependency health.",
        ),
        (
            "Semantic Edit Engine", "emerging",
            "Applies language-aware operations such as append function, replace function, add import, and add test.",
            "File identity, expected AST shape, requested symbol contract, bounded generated fragment.",
            "Proposed file artifact and exact semantic delta.",
            "AST/CST parsing, typed operation preconditions, formatting, before/after semantic diff.",
            "Wrong symbol, signature, extra top-level nodes, stale hash, or out-of-scope path is rejected.",
# <<<MISSING_LINE_1353>>>
# <<<MISSING_LINE_1354>>>
# <<<MISSING_LINE_1355>>>
# <<<MISSING_LINE_1356>>>
# <<<MISSING_LINE_1357>>>
# <<<MISSING_LINE_1358>>>
# <<<MISSING_LINE_1359>>>
# <<<MISSING_LINE_1360>>>
# <<<MISSING_LINE_1361>>>
# <<<MISSING_LINE_1362>>>
# <<<MISSING_LINE_1363>>>
# <<<MISSING_LINE_1364>>>
# <<<MISSING_LINE_1365>>>
# <<<MISSING_LINE_1366>>>
# <<<MISSING_LINE_1367>>>
# <<<MISSING_LINE_1368>>>
# <<<MISSING_LINE_1369>>>
# <<<MISSING_LINE_1370>>>
# <<<MISSING_LINE_1371>>>
# <<<MISSING_LINE_1372>>>
# <<<MISSING_LINE_1373>>>
# <<<MISSING_LINE_1374>>>
    # Chapter 6 - components
# <<<MISSING_LINE_1376>>>
# <<<MISSING_LINE_1377>>>
# <<<MISSING_LINE_1378>>>
# <<<MISSING_LINE_1379>>>
# <<<MISSING_LINE_1380>>>
# <<<MISSING_LINE_1381>>>
# <<<MISSING_LINE_1382>>>
# <<<MISSING_LINE_1383>>>
# <<<MISSING_LINE_1384>>>
# <<<MISSING_LINE_1385>>>
# <<<MISSING_LINE_1386>>>
# <<<MISSING_LINE_1387>>>
# <<<MISSING_LINE_1388>>>
# <<<MISSING_LINE_1389>>>
# <<<MISSING_LINE_1390>>>
# <<<MISSING_LINE_1391>>>
# <<<MISSING_LINE_1392>>>
# <<<MISSING_LINE_1393>>>
# <<<MISSING_LINE_1394>>>
# <<<MISSING_LINE_1395>>>
# <<<MISSING_LINE_1396>>>
# <<<MISSING_LINE_1397>>>
# <<<MISSING_LINE_1398>>>
# <<<MISSING_LINE_1399>>>
# <<<MISSING_LINE_1400>>>
# <<<MISSING_LINE_1401>>>
            "Allowlisted root, manifests, ASTs, imports, configuration, test discovery, diagnostics.",
# <<<MISSING_LINE_1403>>>
# <<<MISSING_LINE_1404>>>
# <<<MISSING_LINE_1405>>>
# <<<MISSING_LINE_1406>>>
# <<<MISSING_LINE_1407>>>
# <<<MISSING_LINE_1408>>>
# <<<MISSING_LINE_1409>>>
# <<<MISSING_LINE_1410>>>
# <<<MISSING_LINE_1411>>>
# <<<MISSING_LINE_1412>>>
# <<<MISSING_LINE_1413>>>
# <<<MISSING_LINE_1414>>>
# <<<MISSING_LINE_1415>>>
# <<<MISSING_LINE_1416>>>
# <<<MISSING_LINE_1417>>>
# <<<MISSING_LINE_1418>>>
# <<<MISSING_LINE_1419>>>
# <<<MISSING_LINE_1420>>>
# <<<MISSING_LINE_1421>>>
# <<<MISSING_LINE_1422>>>
# <<<MISSING_LINE_1423>>>
# <<<MISSING_LINE_1424>>>
# <<<MISSING_LINE_1425>>>
# <<<MISSING_LINE_1426>>>
# <<<MISSING_LINE_1427>>>
# <<<MISSING_LINE_1428>>>
# <<<MISSING_LINE_1429>>>
# <<<MISSING_LINE_1430>>>
# <<<MISSING_LINE_1431>>>
# <<<MISSING_LINE_1432>>>
# <<<MISSING_LINE_1433>>>
# <<<MISSING_LINE_1434>>>
# <<<MISSING_LINE_1435>>>
# <<<MISSING_LINE_1436>>>
# <<<MISSING_LINE_1437>>>
# <<<MISSING_LINE_1438>>>
# <<<MISSING_LINE_1439>>>
# <<<MISSING_LINE_1440>>>
# <<<MISSING_LINE_1441>>>
# <<<MISSING_LINE_1442>>>
# <<<MISSING_LINE_1443>>>
# <<<MISSING_LINE_1444>>>
# <<<MISSING_LINE_1445>>>
# <<<MISSING_LINE_1446>>>
# <<<MISSING_LINE_1447>>>
# <<<MISSING_LINE_1448>>>
# <<<MISSING_LINE_1449>>>
# <<<MISSING_LINE_1450>>>
# <<<MISSING_LINE_1451>>>
# <<<MISSING_LINE_1452>>>
# <<<MISSING_LINE_1453>>>
# <<<MISSING_LINE_1454>>>
# <<<MISSING_LINE_1455>>>
# <<<MISSING_LINE_1456>>>
# <<<MISSING_LINE_1457>>>
# <<<MISSING_LINE_1458>>>
# <<<MISSING_LINE_1459>>>
# <<<MISSING_LINE_1460>>>
# <<<MISSING_LINE_1461>>>
# <<<MISSING_LINE_1462>>>
# <<<MISSING_LINE_1463>>>
# <<<MISSING_LINE_1464>>>
# <<<MISSING_LINE_1465>>>
# <<<MISSING_LINE_1466>>>
# <<<MISSING_LINE_1467>>>
# <<<MISSING_LINE_1468>>>
# <<<MISSING_LINE_1469>>>
# <<<MISSING_LINE_1470>>>
# <<<MISSING_LINE_1471>>>
# <<<MISSING_LINE_1472>>>
# <<<MISSING_LINE_1473>>>
# <<<MISSING_LINE_1474>>>
# <<<MISSING_LINE_1475>>>
# <<<MISSING_LINE_1476>>>
# <<<MISSING_LINE_1477>>>
            "Transition coverage, concurrency convergence, replay behavior, stale-binding rejection.",
# <<<MISSING_LINE_1479>>>
# <<<MISSING_LINE_1480>>>
# <<<MISSING_LINE_1481>>>
# <<<MISSING_LINE_1482>>>
# <<<MISSING_LINE_1483>>>
# <<<MISSING_LINE_1484>>>
# <<<MISSING_LINE_1485>>>
# <<<MISSING_LINE_1486>>>
# <<<MISSING_LINE_1487>>>
# <<<MISSING_LINE_1488>>>
# <<<MISSING_LINE_1489>>>
# <<<MISSING_LINE_1490>>>
# <<<MISSING_LINE_1491>>>
# <<<MISSING_LINE_1492>>>
# <<<MISSING_LINE_1493>>>
# <<<MISSING_LINE_1494>>>
# <<<MISSING_LINE_1495>>>
            "Dataset coverage, leakage audits, class balance, temporal transfer, label confidence.",
# <<<MISSING_LINE_1497>>>
# <<<MISSING_LINE_1498>>>
# <<<MISSING_LINE_1499>>>
# <<<MISSING_LINE_1500>>>
# <<<MISSING_LINE_1501>>>
# <<<MISSING_LINE_1502>>>
# <<<MISSING_LINE_1503>>>
# <<<MISSING_LINE_1504>>>
# <<<MISSING_LINE_1505>>>
# <<<MISSING_LINE_1506>>>
# <<<MISSING_LINE_1507>>>
# <<<MISSING_LINE_1508>>>
# <<<MISSING_LINE_1509>>>
# <<<MISSING_LINE_1510>>>
# <<<MISSING_LINE_1511>>>
# <<<MISSING_LINE_1512>>>
# <<<MISSING_LINE_1513>>>
            "Library utility, redundancy, coverage, survival, maintenance cost, model independence.",
# <<<MISSING_LINE_1515>>>
# <<<MISSING_LINE_1516>>>
# <<<MISSING_LINE_1517>>>
# <<<MISSING_LINE_1518>>>
            "Canonical read model, streamed chat events, project bindings, action contracts.",
            "One coherent project card, exact action buttons, safe errors, visible retry and validation evidence.",
            "Typed client mapping with no inferred lifecycle success; domain components and hooks replace a monolith over time.",
            "Missing bindings hide unsafe actions; typed backend errors are shown without exposing secrets.",
            "Action visibility, reload correctness, stale-action handling, task completion time, user comprehension.",
        ),
    ]
    for comp in components:
        add_component(s, *comp)

    # Chapter 7
    chapter(
        s, 7, "The Procedural Intelligence Compiler",
        "The research core compiles evidence, experience, procedures, applicability, failures, and measured performance into governed procedural artifacts.",
    )
    add_figure(s, compiler_diagram(), "Figure 7.1 - Candidates pass through typing, identity analysis, simulation, replay, transfer, and governed promotion.")
    s.append(heading("7.1 Scope and compiler inputs", 2))
    s.append(para(
        "The Procedural Intelligence Compiler is the umbrella research system. Its Capability Compiler is the "
        "subsystem that emits typed procedural IR. Source material is not raw chat: a compilation batch consists "
        "of immutable experiences linking pre-decision features, alternatives, interventions, exact artifacts, "
        "validation, approvals, failures, retries, user observations, and outcomes. Both positive and negative "
        "episodes are required. The compiler produces recommendations and candidate artifacts; it never grants "
        "production authority."
    ))
    s.append(heading("7.2 Formal capability object", 2))
    s.append(callout(
        "Frozen decomposition",
        "<b>C = (A, P, I, V)</b>, where A is applicability, P is the parameterised procedure, I is the invariant "
        "set and causal safety argument, and V is the independent verification contract.",
        fill=MINT,
        accent=TEAL,
    ))
    s.append(table([
        ["Element", "Question answered", "Versioning consequence"],
        ["A - Applicability", "Under which evidence-backed contexts may the capability activate?", "Predicate changes require retroactive episode re-evaluation."],
        ["P - Procedure", "Which typed operations and data dependencies perform the work?", "A causal procedure change may require a new identity, not a minor revision."],
        ["I - Invariants", "Why should scope, preservation, authority, and safety remain valid?", "An invariant break is veto-grade negative evidence."],
        ["V - Verification", "Which independent checks establish an acceptable result?", "Verifier changes invalidate prior transfer claims until recomputed."],
    ], [1.25 * inch, 2.85 * inch, CONTENT_W - 4.1 * inch], small=True))
    s.append(para(
        "The IR is a typed directed acyclic graph over trusted atomic operations. Parameters represent paths, "
        "symbols, signatures, framework objects, and bounded model slots. Evidence contracts state what must be "
        "known and fresh. Authority contracts state what remains separately approved. A model slot may generate a "
        "small fragment, but its output remains untrusted until P, I, and V are satisfied."
    ))
    s.append(code_block("""
capability add_fastapi_endpoint@1
  identity:
    procedure_family: typed_route_schema_test
    invariant_family: preserve_auth_scope_and_registration
    verification_family: focused_api_contract_v2
  applicability:
    language == python
    framework == fastapi
    router_registration == present
    test_client == present
  procedure:
    router <- locate_router(route_path)
    schema <- construct_typed_schema(contract)
    endpoint <- insert_typed_endpoint(router, schema, contract)
    test <- add_focused_api_test(endpoint, schema)
  invariants:
    approved_paths_only
    preserve_authentication
    exact_symbol_and_registration
  verification:
    child_validators
    integration_names_match
    focused_test_fails_before_and_passes_after
"""))
    s.append(heading("7.3 Capability identity and evolution", 2))
    s.append(para(
        "One capability may span different parameterised implementations only while they share one causal procedure "
        "family, one safety argument, and one verification contract. Source similarity, task labels, trajectory "
        "embeddings, or coincidentally similar patches are insufficient. Applicability predicates may select and "
        "parameterise P. They may not encode an alternative P inside increasingly elaborate conditions."
    ))
    s.append(table([
        ["Decision", "Use when", "Required output"],
        ["Refine applicability", "P, I, and V remain one causal account but the activation region was too broad or incomplete.", "New predicate version; recomputed historical membership, coverage, reliability, and transfer."],
        ["Split capability", "A minimal witness shows a different P, I, or V is required.", "Probationary child plus distinguishing witness; parent remains authoritative until evidence accumulates."],
        ["Compose capabilities", "Existing capabilities form a reusable higher-order workflow.", "Composite contract, integration verifier, dependency lifecycle rules, and independent benchmark."],
        ["Unresolved", "Current vocabulary or bounded search cannot decide identity.", "Preserve hypotheses and counterexamples; do not force merge or split."],
    ], [1.25 * inch, 2.7 * inch, CONTENT_W - 3.95 * inch], small=True))
    s.append(callout(
        "Unification rule",
        "Successful unification is evidence about shared structure. Failed bounded unification is evidence about "
        "the limitations of the current compiler. It leaves identity unresolved or creates, at most, a provisional "
        "split candidate.",
        fill=PALE_GOLD,
        accent=GOLD,
    ))
    s.append(heading("7.4 Compilation passes and epistemic outputs", 2))
    passes = [
        ("Experience normalization", "Preserve raw features, versions, alternatives, interventions, failures, and evidence identity."),
        ("Pattern detection", "Find recurring operation and dependency structures across independent episodes."),
        ("Bounded anti-unification", "Propose typed parameters without treating search failure as a split decision."),
        ("Identity analysis", "Compare causal P, I, and V; emit refine, split-candidate, compose, merge-candidate, or unresolved."),
        ("Applicability inference", "Identify evidence that separates valid activation, correct abstention, and false applicability."),
        ("Evidence-contract inference", "Determine which observations were necessary and which vocabulary version expressed them."),
        ("Safety and authority typing", "Reject non-allowlisted operations, hidden authority, unsafe paths, and unverifiable model slots."),
        ("Vocabulary adequacy check", "Detect repeated ambiguity caused by an atomic operation that is too coarse."),
        ("Simulation and mutation", "Exercise boundary conditions, invariant attacks, and generated counterexamples."),
        ("Historical replay", "Re-run against immutable snapshots and recompute membership under current A."),
        ("Held-out transfer", "Test unchanged C on uninvolved contexts with declared changed dimensions."),
        ("Promotion dossier", "Freeze versions, hashes, witnesses, dependencies, metrics, thresholds, and limitations."),
    ]
    for idx, (name, desc) in enumerate(passes, 1):
        s.append(numbered(idx, f"<b>{name}:</b> {desc}"))
    s.append(heading("7.5 Composite capability contract", 2))
    s.append(callout(
        "Emergent verification",
        "<b>V(composite) = union of child verification obligations + V(integration).</b> Passing every child "
        "verifier is insufficient when names, schemas, registrations, imports, or lifecycle assumptions disagree.",
        fill=MINT,
        accent=TEAL,
    ))
    s.append(table([
        ["Dependency condition", "Composite consequence"],
        ["Any child is probationary", "Composite is at most probationary."],
        ["Any child is degraded", "Block automatic selection; allow explicit research replay only."],
        ["Any child is revoked", "Disable the composite immediately."],
        ["Child version changes P, I, or V", "Invalidate integration assessment until replayed and benchmarked."],
    ], [2.0 * inch, CONTENT_W - 2.0 * inch]))
    s.append(heading("7.6 Lifecycle, probation, and distinguishing witnesses", 2))
    s.append(table([
        ["State", "Meaning", "Permitted use"],
        ["Observed pattern", "Repeated structure detected.", "Research analysis only."],
        ["Candidate", "Typed C=(A,P,I,V) exists.", "Simulation only."],
        ["Probationary child", "Proposed split with a minimal distinguishing witness.", "Targeted replay and held-out transfer only."],
        ["Replay-verified", "Historical snapshots and recomputed predicate membership pass.", "Held-out benchmark only."],
        ["Experimental", "Minimum transfer, precision, and safety thresholds pass.", "Shadow or explicitly opted-in projects."],
        ["Production", "Independent reuse and integration obligations pass.", "Normal strategy candidate."],
        ["Degraded", "Canary, dependency, or live reliability declined.", "No automatic selection."],
        ["Deprecated", "Superseded or no longer useful.", "Replay and audit only."],
        ["Revoked", "Unsafe, invalid, or dependency compromised.", "Never executable."],
    ], [1.2 * inch, 2.6 * inch, CONTENT_W - 3.8 * inch], small=True))
    s.append(para(
        "A distinguishing witness records the smallest context pair for which the parent and proposed child require "
        "different procedure, invariant, or verification behavior. A child earns independent identity only through "
        "repeated success in its region, repeated parent failure or invariant-breaking exceptions, held-out transfer, "
        "and a surviving witness. Mere clustering does not create identity."
    ))
    s.append(heading("7.7 Operation-vocabulary evolution", 2))
    s.append(para(
        "Compiler decisions depend on the granularity of trusted atomic operations. An operation such as "
        "insert_code may conceal causally different actions such as append_top_level_symbol and insert_class_member. "
        "Repeated unresolved identity, verifier branching, or invariant ambiguity inside one operation is evidence "
        "that the vocabulary needs human refinement. The compiler records a vocabulary-revision request; it cannot "
        "silently add or redefine trusted operations. Every experience records the operation-vocabulary version."
    ))
    s.append(heading("7.8 Why generated Python is excluded", 2))
    s.append(para(
        "Allowing the compiler to write arbitrary Python into Astra's trusted kernel would make the learner an "
        "authority escalation mechanism. A restricted DSL keeps the attack surface finite and makes static "
        "analysis, replay, provenance, dependency tracking, and revocation possible. New atomic operations may be "
        "added only through reviewed engineering work and the canonical project-control path."
    ))

    # Chapter 8
    chapter(
        s, 8, "Experience, Memory, and Learning Algorithms",
        "Astra learns decisions and procedures from outcomes. It does not confuse an archive of conversations with intelligence.",
    )
    add_figure(s, experience_loop_diagram(), "Figure 8.1 - Outcome-grounded continual improvement loop.")
    s.append(heading("8.1 What counts as experience", 2))
    s.append(para(
        "An experience is not a successful edit and not a chat transcript. It is an immutable episode joining the "
        "pre-outcome repository profile, raw feature values available at decision time, evidence and operation "
        "vocabulary versions, alternatives shown, selected strategy and capability versions, intervention, exact "
        "execution and validation records, user observations, terminal outcome, and derived attribution hypotheses. "
        "Rejected plans, presentation failures, clarification conversations, failed retries, abstentions, and "
        "invalid evaluations are experiences when their provenance is complete."
    ))
    s.append(callout(
        "Ownership rule",
        "Outcome history belongs to immutable experiences, not permanently to the capability version that happened "
        "to process them. Capability performance is a recomputable projection.",
        fill=MINT,
        accent=TEAL,
    ))
    s.append(heading("8.2 Distinct stores and authority", 2))
    s.append(table([
        ["Store", "Purpose", "Mutation policy"],
        ["Experience ledger", "Canonical episodes, observations, context, interventions, and consequences.", "Append-only; corrections are new records."],
        ["Active memory view", "Task-relevant retrieval over decisions and repository conventions.", "Recomputed and rankable; entries can become inactive."],
        ["Preference quarantine", "Confirmed, scoped, testable output constraints for the local user.", "Derived and deletable; excluded from capability compilation and export."],
        ["Capability library", "Executable procedural artifacts that survived promotion.", "Versioned; superseded, deprecated, or revoked explicitly."],
    ], [1.3 * inch, 2.75 * inch, CONTENT_W - 4.05 * inch], small=True))
    s.append(heading("8.3 Retroactive applicability evaluation", 2))
    s.append(para(
        "When C@2 refines the applicability predicate of C@1, Astra must neither inherit every parent episode nor "
        "discard all history. It re-evaluates the stored pre-outcome evidence of each episode under A@2 and inherits "
        "exactly those episodes that satisfy the new predicate. This requires raw feature values, extractor identity, "
        "evidence-vocabulary version, profile version, and missingness to remain available. Coverage, reliability, "
        "transfer, promotion, and degradation statistics are then recomputed."
    ))
    s.append(heading("8.4 Rejection attribution and interventions", 2))
    s.append(callout(
        "Attribution rule",
        "<b>Feedback is an observation. Attribution is a hypothesis. Intervention and repeated evidence justify "
        "learning.</b> A selected rejection reason is not ground truth.",
        fill=PALE_GOLD,
        accent=GOLD,
    ))
    s.append(table([
        ["Hypothesis", "Possible intervention", "Evidence status"],
        ["Intent error", "Re-parse or ask a targeted clarification before planning.", "supported / contradicted / unresolved"],
        ["Plan error", "Re-plan with corrected scope or dependencies.", "supported / contradicted / unresolved"],
        ["Preference mismatch", "Confirm a visible scoped output constraint.", "supported / contradicted / unresolved"],
        ["Context change", "Refresh repository evidence; treat as a new episode if material.", "supported / contradicted / unresolved"],
        ["Interaction cost", "Reduce batch size or approval burden.", "supported / contradicted / unresolved"],
        ["Presentation failure", "Re-narrate the unchanged valid plan.", "supported / contradicted / unresolved"],
    ], [1.5 * inch, 2.9 * inch, CONTENT_W - 4.4 * inch], small=True))
    s.append(para(
        "Typed rejection menus are measurement instruments and must be calibrated. They require a free-text Other "
        "option, preservation of alternatives shown, comparison with subsequent behavior, and downgrade of "
        "unreliable session-level reason selection. Numeric attribution probabilities are prohibited until a "
        "calibration dataset and reliability analysis exist. Repository-convention mismatch is recorded under "
        "context change or preference mismatch according to whether the facts or the user's output constraints moved."
    ))
    s.append(heading("8.5 Preference quarantine", 2))
    s.append(table([
        ["Rule", "Normative consequence"],
        ["Testable constraint only", "Record output constraints such as patch size, narration detail, or test style; never infer psychological traits."],
        ["Lowest authority", "Preferences cannot override evidence, repository conventions, safety, scope, approval, or verification."],
        ["Confirmation before influence", "First proposed use is visible and requires confirmation before graduating to configuration."],
        ["Explicit scope", "Scope is this repository, all repositories, or this task family; N=1 observation cannot establish a general trait."],
        ["Compilation quarantine", "Preference records are excluded from capability synthesis, benchmark export, and public datasets."],
        ["Deletion semantics", "Raw private observations remain in the append-only ledger; deleting preference means stop deriving and stop applying the view."],
    ], [1.75 * inch, CONTENT_W - 1.75 * inch], small=True))
    s.append(heading("8.6 Strategy ranker", 2))
    s.append(para(
        "The first learned production model should be small and transparent. Candidate features include task family, "
        "repository framework, evidence completeness, number of affected files, available semantic operations, "
        "historical strategy outcomes, model availability, and validation cost. Logistic regression or gradient-"
        "boosted trees provide strong baselines. A contextual bandit becomes useful when Astra must balance known "
        "strategies with bounded exploration, but exploration must never cross hard safety constraints."
    ))
    s.append(heading("8.7 Retrieval ranker", 2))
    s.append(para(
        "Retrieval should begin with exact paths, symbols, imports, test links, and BM25. A learned reranker may then "
        "use outcome evidence to reorder candidates. Labels should reflect whether an artifact was actually used in "
        "a successful decision, not merely whether it appeared in the prompt. The benchmark must include relevant "
        "and irrelevant near-neighbor files to measure false confidence."
    ))
    s.append(heading("8.8 Memory utility model", 2))
    s.append(para(
        "Dynamic memory is a ranking problem over immutable evidence. Features include recency, reuse count, "
        "repository similarity, task similarity, outcome quality, contradiction, and marginal contribution to a "
        "successful plan. A memory can leave the active view without being deleted. This separation permits later "
        "recomputation if the ranker was biased."
    ))
    s.append(heading("8.9 Failure predictor", 2))
    s.append(para(
        "Failure DNA should be structured into a taxonomy: wrong path, stale binding, missing import, wrong symbol, "
        "schema failure, semantic loss, test failure, insufficient evidence, provider failure, resource rejection, "
        "and user rejection. The predictor estimates likely failures before generation and selects preventive "
        "validators or alternative strategies. It remains advisory; detected real failures are determined by "
        "guards and tests."
    ))
    s.append(heading("8.10 Confidence without theater", 2))
    s.append(para(
        "A confidence percentage is meaningful only if calibrated. Astra should report hard evidence separately "
        "from predicted success. Syntax valid and tests passed are observations. A 72% probability of held-out "
        "success is a model estimate. Calibration should be measured with reliability curves, Brier score, and "
        "expected calibration error. No predicted probability converts a failing check into a pass."
    ))
    s.append(heading("8.11 Learning vague intent", 2))
    s.append(para(
        "A request such as 'make the API architecture better' can be handled progressively. Astra detects repository "
        "symptoms, retrieves prior decisions, proposes bounded interpretations, asks a targeted question, and records "
        "which interpretation the user selected. Over time it learns a repository-specific vocabulary and a "
        "clarification policy. It does not pretend that a small classifier has open-ended human understanding."
    ))

    # Chapter 9
    chapter(
        s, 9, "Repository Intelligence and Evidence",
        "For coding systems, structure is often more valuable than more prose. Astra constructs a compact architectural map before invoking a language model.",
    )
    add_figure(s, repository_graph_diagram(), "Figure 9.1 - Example task-specific repository graph and evidence package.")
    s.append(heading("9.1 Repository profile", 2))
    s.append(para(
        "A repository profile is a versioned collection of measurable facts: languages, frameworks, package "
        "managers, test runners, typing mode, directory conventions, import topology, route registration, schema "
        "patterns, formatting tools, and approved validation recipes. The profile is not a personality metaphor. "
        "Every attribute must identify its extractor, evidence artifact, freshness, and confidence class."
    ))
    s.append(heading("9.2 Evidence package", 2))
    s.append(code_block("""
EvidencePackage
  identity:
    project_run_id
    repository_root_fingerprint
    manifest_id
    content_hash

  task:
    intent_id
    requested_symbols
    approved_paths

  structure:
    target_symbols
    callers
    imports
    tests
    configuration

  source:
    bounded exact excerpts
    file_sha256
    source_spans

  limits:
    maximum_files
    maximum_characters
    exclusions
    completeness
    completeness
"""))
    s.append(heading("9.3 Context compression", 2))
    s.append(para(
        "Compression is not an SLM summary of a hundred files. It is the deterministic reduction of a repository "
        "to the symbols, dependencies, tests, configurations, and conventions that could influence the task. "
        "Language summarization may be added after this reduction for human readability, but the structural package "
        "remains authoritative."
    ))
    s.append(heading("9.4 Retrieval stages", 2))
    stages = [
        "Exact binding: selected project, explicit path, mentioned symbol, known test.",
        "Structural expansion: callers, callees, imports, registration sites, configuration.",
        "Sparse ranking: lexical and BM25 relevance over bounded project artifacts.",
        "Outcome reranking: previous evidence utility conditioned on task and repository profile.",
        "Optional compact embedding reranking: used only when it beats the deterministic baseline.",
        "Evidence validation: identities, freshness, completeness, and scope are rechecked.",
    ]
    for i, item in enumerate(stages, 1):
        s.append(numbered(i, item))
    s.append(heading("9.5 Relevant research", 2))
    s.append(para(
        "RepoGraph reports that repository-level graph guidance improves multiple software-engineering methods [R3]. "
        "Repository Intelligence Graph argues for deterministic, evidence-backed architectural maps and reports "
        "accuracy and efficiency gains across coding assistants [R4]. Repository-level neural code search shows "
        "that sparse retrieval followed by neural reranking can improve file localization [R5]. Astra adopts these "
        "as design evidence, not as proof that its own implementation will achieve the same gains."
    ))
    # Chapter 10
    # Chapter 10
    chapter(
        s, 10, "Safety, Approval, and Self-Modification",
        "Astra may improve itself only through the same canonical project path used for any other repository, with stricter protected-module policy.",
    )
    s.append(heading("10.1 Authority matrix", 2))
    s.append(table([
        ["Component", "May propose", "May validate", "May approve", "May mutate"],
        ["SLM", "Yes, bounded", "No", "No", "No"],
        ["Ranker", "Select allowlisted candidate", "No", "No", "No"],
        ["Capability compiler", "Candidate DSL artifact", "Runs benchmark through verifier", "No", "No"],
        ["Deterministic verifier", "No", "Yes", "No", "No"],
        ["User/authorized actor", "Yes", "May supply manual evidence", "Yes", "Through canonical command"],
        ["Project worker", "No", "Executes approved checks", "No", "Only after exact authority"],
        ["ProjectControlPlane", "No", "Accepts fresh evidence", "Enforces grants", "Authorizes transition"],
    ], [1.25 * inch, 1.45 * inch, 1.35 * inch, 1.0 * inch, CONTENT_W - 5.05 * inch], small=True))
    s.append(heading("10.2 Automatic focused testing", 2))
    s.append(para(
        "Focused testing may be automatic because Astra derives the command from a deterministic validation recipe, "
        "not from unconstrained model text. The command runs against a disposable network-disabled snapshot with no "
        "host secrets, package installation, or host fallback. CPU, memory, time, process count, and output are "
        "bounded. The exact command and result remain visible. Patch application remains a separate explicit approval."
    ))
    s.append(heading("10.3 Self-update policy", 2))
    s.append(para(
        "Astra can work on its own repository, but self-update does not mean self-authority. Small changes pass "
        "through plan approval, patch approval, isolated validation, immutable artifacts, and post-apply verification. "
        "Modules that implement approval, workspace safety, artifact integrity, isolation, or verification are "
        "protected. Changes to protected modules require stronger human review and cannot be generated by a learned "
        "capability in unattended mode."
    ))
    s.append(heading("10.4 Threats specific to compiled capabilities", 2))
    threats = [
        ("Overgeneralization", "A procedure learned from one framework activates in a superficially similar but incompatible repository."),
        ("Poisoned experience", "A successful outcome contains an unsafe or accidental workaround."),
        ("Benchmark leakage", "Candidate construction uses examples that later appear in evaluation."),
        ("Capability explosion", "Thousands of narrow procedures increase ambiguity and maintenance cost."),
        ("Authority smuggling", "A candidate encodes a command or path outside the trusted operation vocabulary."),
        ("Self-confirming policy", "The router repeatedly selects one strategy and never gathers evidence about alternatives."),
        ("Dependency drift", "A trusted atomic operation changes semantics while compiled dependants remain active."),
    ]
    s.append(table(
        [["Threat", "Required control"]] + [[a, b] for a, b in threats],
        [1.35 * inch, CONTENT_W - 1.35 * inch],
    ))
    s.append(heading("10.5 Fail-closed principle", 2))
    s.append(callout(
        "Non-negotiable",
        "Provider unavailable, GPU busy, insufficient memory, stale evidence, invalid schema, malformed model output, "
        "unknown DSL, failed replay, out-of-scope path, missing approval, and isolation failure all remain typed "
        "non-success outcomes. Learning optimizes inside these boundaries; it does not reinterpret them.",
        fill=HexColor("#F8E1E1"),
        accent=RED,
    ))

    # Chapter 11
    chapter(
        s, 11, "Experimental Framework",
        "The project becomes scientific only when baselines, chronological splits, negative outcomes, ablations, and falsification criteria are defined before results are known.",
    )
    s.append(heading("11.1 Baseline ladder", 2))
    s.append(table([
        ["System", "What it contains", "Purpose"],
        ["B0 Static deterministic", "Rules, AST operations, fixed capability catalog, no model.", "Measures non-neural competence."],
        ["B1 Static + SLM", "B0 plus bounded local synthesis.", "Measures model contribution."],
        ["B2 Memory only", "B1 plus retrieval of prior outcomes/workflows.", "Separates recall from learning."],
        ["B3 Strategy ranker", "B2 plus learned selection among fixed capabilities.", "Measures policy learning."],
        ["B3.5 Prompt-guidance control", "Same discovered procedures rendered as prompt guidance instead of deterministic execution.", "Isolates typed activation from textual skill libraries (CODESKILL / SWE-Skills-Bench contrast)."],
        ["B4 Capability compiler", "B3 plus promoted compiled procedures.", "Tests the central contribution."],
        ["B5 Model swap", "B4 with a different or absent SLM.", "Tests model independence."],
    ], [1.25 * inch, 3.15 * inch, CONTENT_W - 4.4 * inch]))
    s.append(heading("11.2 Task families", 2))
    task_families = [
        "Static defect detection and deterministic repair.",
        "Add one Python function while preserving existing symbols.",
        "Modify one existing function under behavioral tests.",
        "Add or extend focused pytest tests.",
        "FastAPI route, schema, service, and test additions.",
        "Configuration changes with typed parsers.",
        "Known diagnostic repair from Ruff or Pyright.",
        "Small multi-file refactors with explicit symbol boundaries.",
        "Ambiguous architectural requests requiring clarification.",
        "Repository-specific repeated workflows suitable for capability compilation.",
        "Adversarial out-of-scope, stale-binding, and malicious capability cases.",
    ]
    for item in task_families:
        s.append(bullet(item))
    s.append(heading("11.3 Chronological protocol", 2))
    protocol = [
        "Freeze model, prompts, atomic capabilities, compiler version, hardware policy, and safety kernel.",
        "Order projects chronologically by repository evolution.",
        "Reserve future episodes as held out before synthesis, vocabulary design, verifier design, predicate refinement, and threshold selection.",
        "Run every system variant with identical task evidence and resource limits.",
        "Record all candidate attempts, rejections, clarifications, model calls, validation results, and user decisions.",
        "Compile only from past projects; never expose future outcomes to the learner.",
        "Evaluate periodically at fixed project counts: 0, 100, 250, 500, 750, and 1,000.",
        "Repeat with capability compilation disabled to detect whether gains come from unrelated code changes.",
    ]
    for i, item in enumerate(protocol, 1):
        s.append(numbered(i, item))
    s.append(heading("11.4 Capability-relative transfer", 2))
    s.append(callout(
        "Formal definition",
        "Transfer is successful reuse of an unchanged capability on a held-out episode whose capability-relevant "
        "context profile differs along declared dimensions, while procedure P, invariants I, and verification V "
        "remain valid.",
        fill=MINT,
        accent=TEAL,
    ))
    s.append(para(
        "<b>d_C(Rs,Rt) = d(pi_C(Rs), pi_C(Rt))</b>. Transfer distance is computed on the projection of repository "
        "features relevant to A, P, I, or V, not on an undifferentiated repository embedding. For a Python-symbol "
        "append capability, import topology, symbol location, container structure, typing mode, and test convention "
        "may matter while business domain may not."
    ))
    s.append(table([
        ["Stratum", "Meaning", "Example displacement"],
        ["0 - Repetition", "No meaningful capability-relative displacement.", "Same repository pattern and context."],
        ["1 - Intra-repository", "Different context within one repository.", "Different package or architectural region."],
        ["2 - Cross-repository ecosystem", "New repository, same language/framework ecosystem.", "FastAPI repository A to FastAPI repository B."],
        ["3 - Cross-framework", "Same language, different framework.", "FastAPI route procedure to Flask-compatible abstraction."],
        ["4 - Cross-language", "Same procedural abstraction, different language.", "Typed handler workflow across Python and TypeScript."],
        ["5 - Cross-domain structural", "Different domain and ecosystem with shared causal structure.", "Highest and most difficult claim."],
    ], [1.25 * inch, 2.5 * inch, CONTENT_W - 3.75 * inch], small=True))
    s.append(para(
        "The stratum is only a summary. Every scientific claim also records the changed-dimension signature, "
        "projection version, source and target profiles, and capability version."
    ))
    s.append(heading("11.5 Held-out provenance and transfer outcomes", 2))
    s.append(para(
        "A target is held out only if it influenced none of capability synthesis, predicate refinement, operation "
        "vocabulary, verifier design, parameter tuning, split/merge decisions, benchmark thresholds, or promotion "
        "policy. Once used for adaptation, it cannot remain transfer evidence for the adapted version."
    ))
    s.append(table([
        ["Outcome", "Interpretation"],
        ["Transfer success", "Capability activates and P, I, and V hold."],
        ["Correct abstention", "A rejects an unsuitable context; this is successful boundary recognition."],
        ["False applicability", "A activates where the capability should not apply."],
        ["Procedural failure", "P fails despite valid applicability."],
        ["Invariant failure", "I is violated; veto-grade safety or preservation evidence."],
        ["Verification failure", "V cannot establish acceptance or exposes an integration defect."],
        ["Invalid evaluation", "Infrastructure, oracle, leakage, or provenance prevents a capability conclusion."],
    ], [1.45 * inch, CONTENT_W - 1.45 * inch], small=True))
    s.append(heading("11.6 Promotion experiment", 2))
    s.append(code_block("""
for candidate in compiler.detect_patterns(history_before_cutoff):
    ir = compiler.abstract(candidate)
    if not safety_typecheck(ir):
        reject("unsafe_ir")
    replay = evaluate(ir, historical_snapshots)
    simulation = evaluate(ir, generated_counterexamples)
    held_out = evaluate(ir, held_out_repositories)
    if promotion_policy.accepts(replay, simulation, held_out):
        publish_versioned_experimental_capability(ir)
"""))
    s.append(heading("11.7 Statistical treatment", 2))
    s.append(para(
        "Task success should be reported with confidence intervals and paired comparisons because each system variant "
        "attempts the same cases. McNemar-style paired analysis is suitable for binary outcomes; bootstrap intervals "
        "can summarize latency and model-call differences. Chronological transfer and forgetting require separate "
        "curves. The unit of analysis must be the independent task or repository, not every validator check inside "
        "one task."
    ))
    s.append(heading("11.8 Falsification criteria", 2))
    s.append(callout(
        "Reject or revise the hypothesis if",
        "compiled capabilities do not improve held-out success over memory/ranking baselines; applicability errors "
        "increase unsafe proposals; gains disappear under model swap; maintenance and regression costs exceed saved "
        "model work; or apparent gains result from benchmark leakage, task duplication, or capability-count inflation.",
        fill=HexColor("#F8E1E1"),
        accent=RED,
    ))

    # Chapter 12
    chapter(
        s, 12, "Metrics and Capability Growth",
        "Capability Growth Rate is useful inventory, but intelligence must be measured by transferable marginal utility, not by accumulating procedural files.",
    )
    s.append(heading("12.1 Core outcome metrics", 2))
    s.append(table([
        ["Metric", "Definition", "Why it matters"],
        ["Task success", "Acceptance tests and canonical verification pass.", "Primary utility."],
        ["Scope precision", "Changed approved artifacts / all changed artifacts.", "Controls unnecessary edits."],
        ["Model dependence", "Model calls, generated characters, GPU seconds.", "Tests resource efficiency."],
        ["Human burden", "Clarifications, approvals, corrections, review time.", "Measures practical usability."],
        ["Coverage(C)", "P(A_C(e) = true) over the evaluation set.", "Separates breadth from conditional success."],
        ["Reliability(C)", "P(Success | A_C(e) = true).", "Prevents narrow predicates from looking like intelligence growth."],
        ["Transfer", "Capability-relative reuse with stratum and changed-dimension signature (Ch 11.4).", "Distinguishes learning from memorization."],
        ["Forgetting", "Loss on previously solved held-out families.", "Measures stability."],
        ["Safety regression", "Invalid activation or blocked unsafe proposal rate.", "Must not worsen."],
    ], [1.2 * inch, 2.55 * inch, CONTENT_W - 3.75 * inch], small=True))
    s.append(heading("12.2 Coverage versus reliability", 2))
    s.append(para(
        "A narrow, highly reliable capability and a broad, moderately reliable capability make different claims. "
        "Reports must publish both <b>Coverage(C) = P(A_C = true)</b> and "
        "<b>Reliability(C) = P(Success | A_C = true)</b>. Predicate narrowing that raises reliability while "
        "silently collapsing coverage is not counted as intelligence growth."
    ))
    s.append(heading("12.3 Versioned transfer assessments", 2))
    s.append(para(
        "Transfer strata are derived views over immutable evidence packages. Every assessment records "
        "evidence-vocabulary version, capability version, profile projection, changed-dimension signature, "
        "stratum, the original assessment, the current recomputation, and "
        "<font name='AstraMono'>assessment_status: current | superseded</font>. When the evidence vocabulary "
        "changes, historical classifications are recomputed rather than silently inherited. Superseded "
        "assessments remain auditable."
    ))
    s.append(heading("12.4 Capability Growth Rate", 2))
    s.append(para(
        "<b>CGR = newly verified production-capability versions / completed chronological projects.</b> CGR reports "
        "how quickly the library changes, but it does not say whether the new capabilities are useful. A high CGR "
        "may indicate fragmentation, overfitting, or poor merge policy."
    ))
    add_figure(s, CHARTS["capability_growth"], "Figure 12.1 - Illustrative capability counts; the numbers are a proposed reporting example.")
    s.append(heading("12.5 Net Verified Capability Gain", 2))
    s.append(para(
        "The stronger metric is Net Verified Capability Gain (NVCG). Its precise aggregation should be validated "
        "rather than chosen for convenience, but it must reward held-out coverage, applicability precision, transfer, "
        "reuse, and measured improvement while penalizing regressions, redundancy, maintenance cost, and deprecation."
    ))
    s.append(code_block("""
NVCG(candidate) =
    held_out_coverage_gain
  * applicability_precision
  * transfer_success
  * marginal_success_improvement
  * independent_reuse_factor
  - regression_penalty
  - redundancy_penalty
  - maintenance_penalty
"""))
    s.append(heading("12.6 Capability dossier", 2))
    s.append(table([
        ["Field", "Example"],
        ["Identity", "add_fastapi_endpoint@1 / content hash"],
        ["Source range", "Projects 120-344, temporal cutoff recorded"],
        ["Dependencies", "locate_router@2, insert_endpoint@1, add_pytest@3"],
        ["Applicability", "FastAPI + APIRouter + Pydantic + TestClient"],
        ["Replay", "48/50 historical snapshots"],
        ["Held-out", "18/20 repositories; 0 scope violations"],
        ["Marginal gain", "+14 percentage points over best fixed strategy"],
        ["Canaries", "router registration, auth preservation, response schema"],
        ["Lifecycle", "experimental -> production -> degraded -> deprecated"],
    ], [1.35 * inch, CONTENT_W - 1.35 * inch]))
    s.append(heading("12.7 Reporting capability growth honestly", 2))
    s.append(para(
        "Every graph must distinguish observed results from illustrative targets. Counts should be accompanied by "
        "activation precision, reuse distribution, redundancy, deprecation, and held-out marginal gain. A library "
        "that stabilizes at 25 broadly useful procedures may be more intelligent than one that grows to 500 brittle "
        "scripts."
    ))

    # Chapter 13
    chapter(
        s, 13, "The Astra Research Laboratory",
        "Production Astra must remain stable while Algorithm Lab explores ranking, compilation, simulation, and new mathematical ideas against reproducible snapshots.",
    )
    s.append(heading("13.1 Separation of concerns", 2))
    s.append(table([
        ["Production Astra", "Algorithm Lab"],
        ["Only promoted capabilities.", "Candidate DSL and experimental compiler passes."],
        ["Reviewed configuration and pinned runtime.", "Offline replay and generated counterexamples."],
        ["No training during user workflow.", "Short bounded local training where appropriate."],
        ["Canonical safety and approval boundaries.", "Cannot grant production authority."],
        ["Stable migrations and compatibility.", "Disposable datasets, models, and metrics."],
    ], [CONTENT_W / 2, CONTENT_W / 2]))
    s.append(heading("13.2 Laboratory artifacts", 2))
    artifacts = [
        "Immutable project snapshots and synthetic repository fixtures.",
        "Temporal outcome datasets with success and failure examples.",
        "Feature definitions and data-quality reports.",
        "Baseline model cards for rankers and predictors.",
        "Compiler versions and candidate-capability provenance.",
        "Replay reports, counterexample suites, and held-out benchmark results.",
        "Promotion recommendations that production may accept or reject.",
    ]
    for item in artifacts:
        s.append(bullet(item))
    s.append(heading("13.3 Hardware-compatible experiments", 2))
    s.append(para(
        "The laptop is sufficient for AST and graph experiments, BM25 retrieval, SQLite analytics, logistic "
        "regression, random forests, gradient-boosted trees, small embeddings on CPU, contextual-bandit simulation, "
        "and bounded local-model inference. Long fine-tuning and architecture training remain operator-run activities "
        "and are not required for the first research phases."
    ))
    s.append(heading("13.4 Future mathematical research", 2))
    s.append(para(
        "Once the procedural baseline is stable, Astra can investigate new representations for repository state, "
        "successor-style predictions over capability graphs, uncertainty-aware routing, program anti-unification, "
        "Bayesian applicability estimation, and compact learned value functions. The correct order is empirical: "
        "first define the decision, baseline, data, and metric; then ask whether a new algorithm beats established "
        "methods on this machine."
    ))
    s.append(heading("13.5 Reproducibility", 2))
    s.append(para(
        "Every experiment should record source commit, dirty-state fingerprint, configuration, random seeds, task "
        "cutoff, model tag, provider version, hardware snapshot, container image digest, compiler version, capability "
        "library hash, and metric implementation version. An unrepeatable improvement is not a promotable result."
    ))

    # Chapter 14
    chapter(
        s, 14, "Implementation Roadmap",
        "The next generation should be delivered as small, reviewable releases that first consolidate authority, then instrument outcomes, then introduce learning in shadow mode.",
    )
    s.append(heading("14.1 Phase plan", 2))
    s.append(table([
        ["Phase", "Objective", "Exit criterion"],
        ["A - Definition", "Freeze Astra v1 scope, canonical owners, terminology, and research contracts.", "Approved architecture decision record and subsystem inventory."],
        ["B - Consolidation", "One model boundary, workflow, retrieval path, worker, and frontend state source.", "Legacy paths cannot mutate canonical state or call providers directly."],
        ["C - Instrumentation", "Complete decision/outcome linkage and failure taxonomy.", "At least 95% of benchmark decisions produce valid outcome records."],
        ["D - Semantic capabilities", "Harden typed Python operations and focused validation.", "Operation-level preservation and adversarial scope tests pass."],
        ["E - Rankers", "Shadow retrieval, strategy, failure, and clarification models.", "Calibrated held-out gains with zero authority changes."],
        ["F - Compiler prototype", "Offline pattern -> candidate IR -> replay pipeline.", "Produces candidates but no production activation."],
        ["G - Promotion", "Held-out benchmark, canaries, lifecycle, and capability registry.", "First experimental capability passes predefined thresholds."],
        ["H - Continual study", "Chronological 100/250/500/1000-project evaluation.", "Peer-reviewable dataset, results, limitations, and ablations."],
    ], [0.9 * inch, 3.45 * inch, CONTENT_W - 4.35 * inch], small=True))
    s.append(heading("14.2 Immediate engineering priorities", 2))
    priorities = [
        "Create a canonical subsystem inventory and classify core, supporting, experimental, legacy, or separate product.",
        "Route every active model invocation through LocalAIService.",
        "Reduce main.py to application composition and split App.tsx into domain components.",
        "Finish intent, decision, and outcome schemas with immutable artifact linkage.",
        "Strengthen append_python_symbol to enforce exact symbol count, name, signature, and top-level-node policy.",
        "Define automatic focused-test recipes as deterministic capability metadata.",
        "Expand benchmark negatives, cross-repository cases, and chronological outcome fixtures.",
        "Implement the capability DSL parser and static type checker before pattern mining.",
    ]
    for i, item in enumerate(priorities, 1):
        s.append(numbered(i, item))
    s.append(heading("14.3 What should not happen next", 2))
    no_next = [
        "Do not add another general orchestrator.",
        "Do not introduce more direct model adapters outside LocalAIService.",
        "Do not build many specialist neural models without comparative benchmarks.",
        "Do not enable unrestricted autonomous shell execution.",
        "Do not train or fine-tune as a substitute for fixing evidence and capability design.",
        "Do not let a candidate capability modify the safety kernel.",
        "Do not perform a single massive repository rewrite.",
    ]
    for item in no_next:
        s.append(bullet(item))
    s.append(heading("14.4 Product boundary recommendation", 2))
    s.append(para(
        "Astra v1 should be a local Python coding assistant. Assignment management, client engagement, general "
        "document automation, training pipelines, and broad specialist experiments should be separated from the "
        "active coding product path unless they directly support the research benchmark. Narrow scope makes the "
        "capability compiler measurable."
    ))

    # Chapter 15
    chapter(
        s, 15, "Risks, Limitations, and Ethical Research Practice",
        "The research is valuable only if it resists inflated novelty claims, benchmark leakage, unsafe self-modification, and confidence numbers without calibration.",
    )
    s.append(heading("15.1 Technical limitations", 2))
    limitations = [
        "A 1.5B model remains weak on broad architecture, unfamiliar APIs, and long multi-file synthesis.",
        "Deterministic capabilities cover only anticipated operation families.",
        "Tests are incomplete specifications; passing them does not prove full correctness.",
        "Repository-specific learning can overfit conventions and transfer poorly.",
        "Capability compilation needs enough independent trajectories; early data will be sparse.",
        "Chronological projects are not identically distributed, complicating causal claims.",
        "Local Docker and Ollama availability introduce operational variability.",
        "The current repository has architectural debt that can confound research measurements.",
    ]
    for item in limitations:
        s.append(bullet(item))
    s.append(heading("15.2 Novelty discipline", 2))
    s.append(para(
        "Astra should not claim to originate workflow memory, skill extraction, self-evolving skill banks, repository "
        "graphs, test-time candidate selection, or continual-learning evaluation. Its contribution must be stated "
        "as a specific architecture and empirical result relative to these baselines. If another system already "
        "implements the same verified compiler pipeline before publication, Astra's value may remain in its low-"
        "resource evaluation, safety integration, DSL, datasets, or negative findings."
    ))
    s.append(heading("15.3 User autonomy and privacy", 2))
    s.append(para(
        "Because Astra operates locally, raw repositories need not leave the machine. Outcome datasets should avoid "
        "secrets and unnecessary source retention. Capability provenance must identify whether procedures came from "
        "private repositories and prevent accidental publication of proprietary patterns or code. The user must be "
        "able to inspect, disable, export, or delete derived learning artifacts subject to integrity requirements."
    ))
    s.append(heading("15.4 Negative results are first-class", 2))
    s.append(para(
        "A capability that fails to transfer, a ranker that does not beat rules, or an embedding model that loses to "
        "BM25 is a useful result. The Algorithm Lab should retain failed hypotheses and publish ablations. The goal "
        "is not to prove Astra intelligent; it is to discover which forms of procedural accumulation work under "
        "real hardware and safety constraints."
    ))

    # Chapter 16
    chapter(
        s, 16, "Conclusion: What Astra Is Trying to Build",
        "Astra is not a compressed frontier model. It is a software system whose verified procedural intelligence can grow while its model remains small and replaceable.",
    )
    s.append(para(
        "The refined Astra project asks a better question than the original attempt to make a small model behave like "
        "a large one. It asks whether software-engineering intelligence can be externalized into deterministic "
        "structure, trusted operations, repository evidence, calibrated decisions, verified procedures, and "
        "chronological experience."
    ))
    s.append(para(
        "The current repository already contains the hardest foundation: canonical project control, exact approval "
        "bindings, immutable artifacts, fail-closed local-AI admission, isolated execution, deterministic analysis, "
        "project retrieval, and a large regression suite. The next step is not more breadth. It is consolidation and "
        "the careful construction of a procedural-learning research boundary."
    ))
    s.append(Paragraph(
        "“Astra does not trust experience. It compiles experience into candidates and trusts only what survives verification.”",
        ST["quote"],
    ))
    s.append(heading("16.1 Success after 1,000 projects", 2))
    s.append(para(
        "A successful Astra after 1,000 projects is not defined by having stored 1,000 conversations. It has a compact "
        "library of versioned procedures that solve recurring task families, accurate boundaries describing when "
        "those procedures apply, evidence contracts describing what must be retrieved, failure models that prevent "
        "known mistakes, and benchmark dossiers proving marginal utility on held-out work. It makes fewer unnecessary "
        "model calls, asks better questions, touches fewer irrelevant files, and remains safe when every learned "
        "component is disabled."
    ))
    s.append(heading("16.2 The research promise", 2))
    s.append(callout(
        "Astra Next",
        "A deterministic software-engineering system that continually compiles verified procedural capabilities "
        "from experience, allowing useful intelligence to accumulate independently of model size while remaining "
        "local, explainable, reversible, and subordinate to explicit human authority.",
        fill=MINT,
        accent=TEAL,
    ))

    # Appendices
    chapter(
        s, "A", "Appendix: Capability DSL Sketch",
        "A restricted declarative language is the boundary between learned procedural structure and trusted implementation.",
    )
    s.append(heading("A.1 Design requirements", 2))
    for item in [
        "Finite, versioned instruction vocabulary.",
        "Typed parameters for paths, symbols, signatures, commands, and artifacts.",
        "No arbitrary code evaluation or shell strings.",
        "Explicit applicability, evidence, scope, validation, and resource contracts.",
        "Static dependency and authority analysis.",
        "Content-addressed immutable versions.",
        "Deterministic compilation into trusted operation handlers.",
        "Replayable execution trace and revocation support.",
    ]:
        s.append(bullet(item))
    s.append(heading("A.2 Expanded schema", 2))
    s.append(code_block("""
capability_schema: astra.capability/v1
capability_id: add_python_function_with_test
version: 3
status: experimental

provenance:
  compiler_version: capability-compiler/0.2
  source_cutoff: 2026-11-30T00:00:00Z
  source_project_count: 46
  content_hash: sha256:...

applicability:
  all:
    - language == python
    - target_file.parse_status == valid
    - requested_symbol.absent == true
    - focused_test_runner == pytest

inputs:
  target_path: SafeProjectPath
  test_path: SafeProjectPath
  symbol: PythonIdentifier
  signature: PythonFunctionSignature
  behavior_contract: TestableBehavior

evidence:
  required:
    - target_file_identity
    - neighboring_functions
    - test_conventions
  maximum_files: 6
  maximum_characters: 24000

procedure:
  - inspect_python_module(target_path)
  - synthesize_python_function(symbol, signature, behavior_contract)
  - validate_exact_top_level_symbol(symbol, signature)
  - append_python_symbol(target_path)
  - synthesize_focused_pytest(test_path, symbol, behavior_contract)
  - validate_scope()
  - run_focused_test(test_path)
  - emit_patch_artifact()

authority:
  patch_application: explicit_user_approval
  command_execution: deterministic_validation_recipe_only
  verification: canonical_verifier_only
"""))

    chapter(
        s, "B", "Appendix: Outcome and Experience Schemas",
        "Learning quality depends on stable structured evidence, including negative outcomes and the decisions that preceded them. Schema version: astra.experience/v1 (Phase 1 charter).",
    )
    s.append(callout(
        "Ownership rule",
        "Outcome history belongs to immutable experiences, not permanently to the capability version that processed them. "
        "Capability statistics, attribution, preferences, and transfer strata are recomputable projections.",
        fill=MINT,
        accent=TEAL,
    ))
    s.append(heading("B.1 Experience record", 2))
    s.append(code_block("""
Experience  # astra.experience/v1  (immutable)
  experience_id
  occurred_at
  project_run_id?
  conversation_id?
  task_family
  intent_id?
  intent_version?
  pre_outcome_repository_profile
  raw_feature_values{}            # enough to evaluate future predicates
  evidence_vocabulary_version
  operation_vocabulary_version
  evidence_artifact_ids[]
  alternatives_shown[]
  selected_strategy
  capability_id?
  capability_version?
  predicate_version?
  model_profile_id?
  prompt_version_id?
  intervention?                   # re-plan | re-narrate | reduce_cost | confirm_preference | ...
  execution_records[]
  validation_results[]
  user_observations[]             # rejection, clarification, correction, ignore
  stated_rejection_reason?
  terminal_outcome
  failure_code?
  held_out_provenance             # none | adaptation | transfer_candidate | disqualified
  transfer_assessment_version?
  preference_influence?           # scoped; never a trait claim
  content_hash
"""))
    s.append(heading("B.2 DecisionOutcome projection", 2))
    s.append(para(
        "DecisionOutcome remains a lean operational projection for ranking and dashboards. It does not replace "
        "the experience package. When fields conflict, the immutable experience is authoritative."
    ))
    s.append(code_block("""
DecisionOutcome  # projection over Experience
  outcome_id
  occurred_at
  source
  run_id?
  task_family?
  intent?
  strategy
  model_profile?
  context_tokens?
  outcome
  failure_reason?
  duration_ms?
  evidence_artifact_id?
"""))
    s.append(heading("B.3 Attribution record", 2))
    s.append(code_block("""
AttributionRecord  # derived; versioned interpretation
  attribution_id
  experience_id
  evidence_vocabulary_version
  hypotheses[]:
    cause: intent_error | plan_error | preference_mismatch
         | context_change | interaction_cost | presentation_failure
    status: supported | contradicted | unresolved
    evidence_refs[]
  selected_menu_reason?           # instrument reading, not ground truth
  free_text_other?
  calibration_notes?
  assessment_status: current | superseded
"""))
    s.append(heading("B.4 Preference quarantine", 2))
    s.append(code_block("""
PreferenceRecord  # derived view; deletable; excluded from compilation/export
  preference_id
  scope: this_repository | all_repositories | this_task_family
  constraint                    # testable output constraint only
  status: proposed | confirmed | declined | deleted
  first_influence_confirmed_at?
  source_experience_ids[]
  # Raw observations remain in the append-only ledger.
  # Deletion means stop deriving and stop applying this view.
"""))
    s.append(heading("B.5 Capability-evolution decision", 2))
    s.append(code_block("""
CapabilityEvolutionDecision
  decision_id
  parent_capability_id
  parent_version
  operation: refine | split | compose | unresolved | parameterisation_gap
  witness?                      # required for split (minimal distinguishing pair)
  child_capability_id?
  child_lifecycle: probationary_child | ...
  predicate_version_after?
  operation_vocabulary_version
  evidence_vocabulary_version
  held_out_transfer_refs[]
  assessment_status: current | superseded
"""))
    s.append(heading("B.6 Transfer assessment", 2))
    s.append(code_block("""
TransferAssessment  # derived; recomputed when vocabularies change
  assessment_id
  experience_id
  capability_id
  capability_version
  evidence_vocabulary_version
  profile_projection_version
  changed_dimension_signature[]
  stratum: 0..5
  outcome: transfer_success | correct_abstention | false_applicability
         | procedural_failure | invariant_failure | verification_failure
         | invalid_evaluation
  original_assessment_id?       # retained when superseded
  assessment_status: current | superseded
"""))
    s.append(heading("B.7 Failure taxonomy", 2))
    s.append(table([
        ["Class", "Examples"],
        ["Understanding", "unknown intent, ambiguous goal, missing slot, user correction"],
        ["Evidence", "missing file, stale manifest, poor retrieval, incomplete profile"],
        ["Strategy", "wrong capability, capability gap, unnecessary model use"],
        ["Generation", "invalid JSON, wrong schema, hallucinated path, semantic mismatch"],
        ["Scope/integrity", "out-of-scope target, stale hash, altered binding, idempotency conflict"],
        ["Validation", "syntax, type, lint, test, semantic preservation, cleanup"],
        ["Resources", "gpu_busy, insufficient_vram, timeout, worker unavailable"],
        ["Provider", "provider_unavailable, model_unavailable, malformed provider response"],
        ["Human", "plan rejected, patch rejected, clarification requested, presentation failure"],
        ["Lifecycle", "cancelled, rollback, superseded, stale action"],
    ], [1.3 * inch, CONTENT_W - 1.3 * inch]))

    chapter(
        s, "C", "Appendix: Promotion Policy",
        "Promotion is a scientific decision supported by a dossier, not a reward for producing a plausible-looking procedure.",
    )
    s.append(heading("C.1 Example minimum thresholds", 2))
    s.append(table([
        ["Criterion", "Illustrative threshold", "Notes"],
        ["Independent source projects", ">= 10", "Avoid one-repository pattern extraction."],
        ["Historical replay success", ">= 95%", "Failures must be classified, not hidden."],
        ["Held-out repositories", ">= 5", "No overlap with compilation sources."],
        ["Applicability precision", ">= 95%", "Wrong activation is costly."],
        ["Scope violations", "0", "Hard rejection criterion."],
        ["Safety regressions", "0", "Hard rejection criterion."],
        ["Marginal success gain", ">= 5 percentage points", "Versus best fixed strategy."],
        ["Independent production reuses", ">= 3", "Required before full production status."],
        ["Canary pass rate", "100%", "Rechecked after dependencies change."],
    ], [1.6 * inch, 1.55 * inch, CONTENT_W - 3.15 * inch]))
    s.append(heading("C.2 Promotion decision record", 2))
    s.append(para(
        "Every decision records compiler version, source cutoff, benchmark version, metrics, known limitations, "
        "reviewer, activation mode, dependencies, rollback procedure, and expiry/review date. Rejection records are "
        "retained so that the compiler does not repeatedly rediscover the same invalid abstraction."
    ))

    chapter(
        s, "D", "Appendix: Benchmark Blueprint",
        "The evaluation suite grows from the existing 40-case deterministic baseline into chronological, transfer, adversarial, and continual-learning studies.",
    )
    s.append(heading("D.1 Dataset partitions", 2))
    s.append(table([
        ["Partition", "Purpose", "Leakage rule"],
        ["Atomic fixtures", "Validate trusted operations.", "May be used during capability implementation."],
        ["Compiler discovery", "Find recurring patterns.", "Strictly before temporal cutoff."],
        ["Replay", "Check historical compatibility.", "Uses only snapshots already observed."],
        ["Development transfer", "Tune thresholds.", "Repositories excluded from discovery."],
        ["Final held-out", "Primary research result.", "Never used for compiler or policy tuning."],
        ["Adversarial", "Scope, authority, poisoning, and overgeneralization.", "Hidden until evaluation."],
    ], [1.25 * inch, 2.35 * inch, CONTENT_W - 3.6 * inch]))
    s.append(heading("D.2 Required ablations", 2))
    for item in [
        "Remove outcome failures and train from successes only.",
        "Replace structural repository evidence with naive text chunks.",
        "Use memory retrieval without compiled execution.",
        "Use strategy ranking without new capabilities.",
        "Disable applicability learning and use broad task labels.",
        "Allow capability count to grow without merge/prune.",
        "Swap or remove the SLM after compilation.",
        "Disable compiled capabilities while preserving all other later code.",
    ]:
        s.append(bullet(item))

    chapter(
        s, "E", "Appendix: Research Decision Taxonomy",
        "A stable vocabulary prevents architecture discussions from collapsing different kinds of learning into the same word.",
    )
    s.append(heading("E.1 System components", 2))
    s.append(table([
        ["Term", "Role"],
        ["Deterministic plan compiler", "Composes already-available capabilities into a bounded task plan; does not invent new procedures."],
        ["Capability compiler", "Creates candidate procedural artifacts from verified experience under a restricted DSL."],
        ["Semantic edit engine", "Implements trusted atomic operations (locate, insert, rewrite, validate) used by capabilities."],
        ["Capability registry", "Resolves immutable, content-addressed capability versions and dependencies."],
        ["Capability library", "Governed collection of promoted (and lifecycle-managed) capabilities."],
        ["Decision engine", "Selects among allowlisted strategies using rules, memory, and optional shadow/promoted rankers."],
    ], [1.7 * inch, CONTENT_W - 1.7 * inch], small=True))
    s.append(heading("E.2 Research objects", 2))
    s.append(table([
        ["Term", "Definition"],
        ["Atomic capability", "Human-reviewed trusted operation implemented in normal code."],
        ["Composite capability", "Declarative composition of atomic capabilities with an integration verifier."],
        ["Candidate capability", "Unpromoted artifact produced by the compiler."],
        ["Applicability model", "Predicts whether a capability is appropriate for a context."],
        ["Evidence contract", "Facts that must be present and fresh before activation."],
        ["Strategy", "A selectable approach to a task; may invoke one or more capabilities."],
        ["Trajectory", "Ordered decisions, operations, observations, failures, and outcomes."],
        ["Experience", "Immutable episode joining context, alternatives, intervention, outcome, and attribution hypotheses."],
        ["Memory", "Derived retrieval view over immutable experience."],
        ["Procedural intelligence", "Executable, tested knowledge of how to perform a task."],
        ["Capability compilation", "Transformation of verified experience into candidate procedural IR and promoted artifacts."],
        ["Safety kernel", "Fixed authority, scope, integrity, approval, isolation, and verification mechanisms."],
    ], [1.55 * inch, CONTENT_W - 1.55 * inch], small=True))

    chapter(
        s, "F", "Appendix: Related Work and Positioning",
        "Astra should be compared against deterministic repair, repository graphs, workflow memory, experience banks, skill libraries, test-time scaling, and continual-learning benchmarks.",
    )
    related = [
        ("Agentless", "Simple localization, repair, and validation without an autonomous tool-planning agent.", "Supports simple structured baselines; Astra adds chronological procedural compilation."),
        ("SWE-agent", "Agent-computer interface designed for repository navigation and editing.", "Supports purpose-built tool interfaces; Astra restricts authority and compiles procedures."),
        ("RepoGraph / RIG", "Repository-level graphs and deterministic architecture maps.", "Direct foundation for repository intelligence and evidence construction."),
        ("S*", "Sequential and parallel test-time scaling with execution-grounded selection.", "Supports bounded candidate retry and verifier-guided selection."),
        ("Agent Workflow Memory", "Induces reusable workflows and retrieves them for future tasks.", "Astra must distinguish executable promoted procedures from remembered workflow text."),
        ("SWE-Exp", "Distills successful and failed repair experiences.", "Supports outcome-grounded experience; Astra adds compiler IR and fixed safety integration."),
        ("SWE-Bench-CL", "Chronological continual-learning evaluation and forgetting metrics.", "Strong template for temporal splits and transfer measurement."),
        ("SkillFoundry", "Mines validated executable skills from heterogeneous scientific resources.", "Closest conceptual neighbor; Astra focuses on real coding outcomes and local constrained execution."),
        ("CODESKILL", "Learns skill extraction and maintenance policy with reinforcement learning.", "Shows skill-bank learning is established; Astra must demonstrate distinct verified compilation value."),
        ("SWE-Skills-Bench", "Paired execution-based evaluation with and without skills.", "Supports marginal-utility testing rather than capability counting."),
    ]
    s.append(table(
        [["Work", "Relevant idea", "Astra positioning"]] + [list(x) for x in related],
        [1.1 * inch, 2.35 * inch, CONTENT_W - 3.45 * inch],
        small=True,
    ))

    chapter(
        s, "G", "Appendix: References",
        "Primary papers and repository evidence used to position the proposal. URLs were verified during manuscript preparation.",
    )
    references = [
        ("R1", "Xia, C. S. et al. Agentless: Demystifying LLM-based Software Engineering Agents. arXiv:2407.01489, 2024. https://arxiv.org/abs/2407.01489"),
        ("R2", "Yang, J. et al. SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering. arXiv:2405.15793, 2024. https://arxiv.org/abs/2405.15793"),
        ("R3", "Ouyang, S. et al. RepoGraph: Enhancing AI Software Engineering with Repository-level Code Graph. arXiv:2410.14684, 2024. https://arxiv.org/abs/2410.14684"),
        ("R4", "Cherny-Shahar, T. and Yehudai, A. Repository Intelligence Graph: Deterministic Architectural Map for LLM Code Assistants. arXiv:2601.10112, 2026. https://arxiv.org/abs/2601.10112"),
        ("R5", "Gandhi, S., Gao, L., and Callan, J. Repository-level Code Search with Neural Retrieval Methods. arXiv:2502.07067, 2025. https://arxiv.org/abs/2502.07067"),
        ("R6", "Li, D. et al. S*: Test Time Scaling for Code Generation. arXiv:2502.14382, 2025. https://arxiv.org/abs/2502.14382"),
        ("R7", "Wang, Z. Z. et al. Agent Workflow Memory. arXiv:2409.07429, 2024. https://arxiv.org/abs/2409.07429"),
        ("R8", "Chen, S. et al. SWE-Exp: Experience-Driven Software Issue Resolution. arXiv:2507.23361, 2025. https://arxiv.org/abs/2507.23361"),
        ("R9", "Joshi, T., Chowdhury, S., and Uysal, F. SWE-Bench-CL: Continual Learning for Coding Agents. arXiv:2507.00014, 2025. https://arxiv.org/abs/2507.00014"),
        ("R10", "Shen, S. et al. SkillFoundry: Building Self-Evolving Agent Skill Libraries from Heterogeneous Scientific Resources. arXiv:2604.03964, 2026. https://arxiv.org/abs/2604.03964"),
        ("R11", "Li, Y. et al. CODESKILL: Learning Self-Evolving Skills for Coding Agents. arXiv:2605.25430, 2026. https://arxiv.org/abs/2605.25430"),
        ("R12", "SWE-Skills-Bench: Do Agent Skills Actually Help in Real-World Software Engineering? arXiv:2603.15401, 2026. https://arxiv.org/abs/2603.15401"),
        ("R13", "SkillX: Automatically Constructing Skill Knowledge Bases for Agents. arXiv:2604.04804, 2026. https://arxiv.org/abs/2604.04804"),
        ("R14", "SkCC: Portable and Secure Skill Compilation for Cross-Framework LLM Agents. arXiv:2605.03353, 2026. https://arxiv.org/abs/2605.03353"),
        ("R15", "Ma, Z. et al. Rethinking Verification for LLM Code Generation: From Generation to Testing. arXiv:2507.06920, 2025. https://arxiv.org/abs/2507.06920"),
        ("R16", "Astra repository README.md, stage0 trust-integrity, stage1 control-plane, stage2 isolation, phase5-9 integration, stabilization checkpoint, and phase0 benchmark artifacts. Inspected 29 July 2026 on commit 9d7b63a41cf4."),
        ("R17", "Astra deterministic-core benchmark phase0.v1. Latest inspected artifact: benchmarks/results/baseline_2026-07-28.json, 40 cases, 40 passed."),
    ]
    for key, ref in references:
        s.append(Paragraph(f"<b>[{key}]</b> {escape(ref)}", ST["reference"]))

    s.append(PageBreak())
    s.append(Spacer(1, 1.2 * inch))
    s.append(Paragraph("End of research blueprint", ST["h1"]))
    s.append(Spacer(1, 0.2 * inch))
    s.append(Paragraph(
        "The next step is to convert this blueprint into an architecture decision record, a canonical subsystem inventory, and a controlled Phase A implementation plan.",
        ST["body"],
    ))
    s.append(callout(
        "Final definition",
        "<b>Astra compiles verified procedural capabilities from experience.</b><br/>"
        "Its intelligence accumulates independently of whichever language model is currently attached.",
        fill=HexColor("#123A55"),
        accent=GOLD,
        dark=True,
    ))
    return s


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    doc = AstraDocTemplate(str(PDF_PATH))
    story = build_story()
    doc.multiBuild(story)
    print(PDF_PATH)
# <<<MISSING_LINE_2538>>>
# <<<MISSING_LINE_2539>>>
# <<<MISSING_LINE_2540>>>
# <<<MISSING_LINE_2541>>>
# <<<MISSING_LINE_2542>>>
# <<<MISSING_LINE_2543>>>
