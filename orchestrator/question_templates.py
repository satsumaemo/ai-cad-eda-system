"""작업 유형별 질문 정의 — 오케스트레이터가 정보 수집 시 참조한다."""

from dataclasses import dataclass, field


@dataclass
class QuestionItem:
    key: str
    question: str
    required: bool
    default: str | None = None


@dataclass
class TaskTemplate:
    task_type: str
    description: str
    questions: list[QuestionItem] = field(default_factory=list)

    @property
    def required_questions(self) -> list[QuestionItem]:
        return [q for q in self.questions if q.required]

    @property
    def optional_questions(self) -> list[QuestionItem]:
        return [q for q in self.questions if not q.required]

    def missing_info(self, provided: dict) -> list[QuestionItem]:
        """제공된 정보에서 빠진 필수 항목을 반환한다."""
        return [q for q in self.required_questions if q.key not in provided]


TASK_TEMPLATES: dict[str, TaskTemplate] = {
    "pcb_layout": TaskTemplate(
        task_type="pcb_layout",
        description="PCB 레이아웃 설계",
        questions=[
            QuestionItem("board_size", "기판 크기(가로×세로)는?", required=True),
            QuestionItem("layer_count", "몇 층 기판인가?", required=True),
            QuestionItem("key_components", "핵심 부품 목록은?", required=True),
            QuestionItem("signal_paths", "주요 신호 경로는?", required=True),
            QuestionItem("power_spec", "전원 사양(전압, 전류)은?", required=True),
            QuestionItem("impedance_control", "임피던스 제어가 필요한가?", required=False, default="없음"),
            QuestionItem("thermal_req", "방열 요구사항은?", required=False, default="자연대류"),
            QuestionItem("drc_custom", "DRC 커스터마이징이 필요한가?", required=False, default="기본 규칙"),
        ],
    ),
    "thermal_design": TaskTemplate(
        task_type="thermal_design",
        description="방열판/열 설계",
        questions=[
            QuestionItem("heat_source", "발열원(부품, 소비전력)은?", required=True),
            QuestionItem("temp_limit", "목표 온도 한계는?", required=True),
            QuestionItem("cooling_method", "냉각 방식은? (자연대류/강제대류/액냉)", required=True),
            QuestionItem("ambient_temp", "주변 온도는?", required=False, default="25°C"),
            QuestionItem("conductivity_req", "열전도율 요구사항은?", required=False, default="없음"),
            QuestionItem("space_constraint", "공간 제약은?", required=False, default="없음"),
        ],
    ),
    "bracket_design": TaskTemplate(
        task_type="bracket_design",
        description="브래킷/기구 설계",
        questions=[
            QuestionItem("load_conditions", "하중 조건(크기, 방향, 종류)은?", required=True),
            QuestionItem("material", "재질은?", required=True),
            QuestionItem("mounting", "장착 방식은?", required=True),
            QuestionItem("space_constraint", "공간 제약은?", required=True),
            QuestionItem("safety_factor", "안전계수는?", required=False, default="2.0"),
            QuestionItem("manufacturing", "제조 방법은?", required=False, default="CNC"),
            QuestionItem("surface_finish", "표면 처리는?", required=False, default="없음"),
        ],
    ),
    "3d_modeling": TaskTemplate(
        task_type="3d_modeling",
        description="3D 모델링 일반",
        questions=[
            QuestionItem("shape_description", "형상 설명 또는 참조는?", required=True),
            QuestionItem("key_dimensions", "핵심 치수는?", required=True),
            QuestionItem("purpose", "용도/기능은?", required=True),
            QuestionItem("tolerance", "공차는?", required=False, default="일반 공차"),
            QuestionItem("material", "재질은?", required=False, default="미정"),
            QuestionItem("post_process", "후가공은?", required=False, default="없음"),
        ],
    ),
    "cfd_analysis": TaskTemplate(
        task_type="cfd_analysis",
        description="유체 해석",
        questions=[
            QuestionItem("fluid_type", "유체 종류는?", required=True),
            QuestionItem("inlet_conditions", "유입 조건은?", required=True),
            QuestionItem("analysis_purpose", "분석 목적은?", required=True),
            QuestionItem("turbulence_model", "난류 모델은?", required=False, default="k-epsilon"),
            QuestionItem("steady_transient", "정상/비정상 해석?", required=False, default="정상"),
            QuestionItem("mesh_density", "메시 밀도는?", required=False, default="중간"),
        ],
    ),
}


def get_template(task_type: str) -> TaskTemplate | None:
    """작업 유형에 해당하는 템플릿을 반환한다."""
    return TASK_TEMPLATES.get(task_type)


def list_task_types() -> list[str]:
    """등록된 작업 유형 목록을 반환한다."""
    return list(TASK_TEMPLATES.keys())
