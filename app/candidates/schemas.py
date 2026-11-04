from typing import Annotated,Literal
from pydantic import Field,FiniteFloat,StrictBool,StrictInt
from ..research.schemas import Strict,Strategy,Risk,Execution

Hash=Annotated[str,Field(pattern=r'^[a-f0-9]{64}$')]
Identifier=Annotated[str,Field(pattern=r'^[a-f0-9]{32}$')]

class Policy(Strict):
    min_validation_days: Annotated[StrictInt,Field(ge=5,le=1000)]=20
    min_fills: Annotated[StrictInt,Field(ge=1,le=10000)]=1
    max_drawdown_pct: Annotated[FiniteFloat,Field(gt=0,le=50)]=10
    min_return_pct: Annotated[FiniteFloat,Field(ge=-100,le=1000)]=0

class Study(Strict):
    name: Annotated[str,Field(min_length=3,max_length=100)]
    dataset_id: Hash
    instrument_ids: Annotated[list[str],Field(max_length=50)]=[]
    train_pct: Annotated[StrictInt,Field(ge=40,le=75)]=60
    validation_pct: Annotated[StrictInt,Field(ge=10,le=35)]=20
    embargo_bars: Annotated[StrictInt,Field(ge=1,le=100)]=2
    capital: Annotated[FiniteFloat,Field(ge=100,le=1000000000)]=1000000
    max_versions: Annotated[StrictInt,Field(ge=1,le=32)]=8
    max_generations: Annotated[StrictInt,Field(ge=1,le=32)]=8
    max_runs: Annotated[StrictInt,Field(ge=9,le=256)]=80
    max_output_tokens: Annotated[StrictInt,Field(ge=1024,le=12000)]=6000
    max_ai_output_tokens: Annotated[StrictInt,Field(ge=1024,le=128000)]=24000
    risk: Risk=Risk()
    execution: Execution=Execution()
    policy: Policy=Policy()

class Candidate(Strict):
    study_id: Identifier
    strategy: Strategy
    hypothesis: Annotated[str,Field(min_length=10,max_length=4000)]
    risks: Annotated[list[Annotated[str,Field(max_length=1000)]],Field(max_length=20)]=[]
    assumptions: Annotated[list[Annotated[str,Field(max_length=1000)]],Field(max_length=20)]=[]
    parent_version_id: Hash|None=None

class Generate(Strict):
    study_id: Identifier
    idea: Annotated[str,Field(min_length=10,max_length=6000)]
    request_id: Annotated[str,Field(min_length=8,max_length=100)]
    parent_version_id: Hash|None=None

class Evaluate(Strict):
    request_id: Annotated[str,Field(min_length=8,max_length=100)]

class Review(Strict):
    action: Literal['APPROVE_PAPER','REJECT','RETIRE']
    reviewer: Annotated[str,Field(min_length=2,max_length=80)]
    note: Annotated[str,Field(min_length=20,max_length=3000)]
    expected_status: Annotated[str,Field(min_length=1,max_length=40)]

class DataReview(Strict):
    reviewer: Annotated[str,Field(min_length=2,max_length=80)]
    note: Annotated[str,Field(min_length=20,max_length=3000)]
    source_and_rights: StrictBool
    calendar_and_actions: StrictBool
    charges_margin_and_settlement: StrictBool
    execution_assumptions: StrictBool

class AIProposal(Strict):
    hypothesis: Annotated[str,Field(min_length=10,max_length=4000)]
    strategy_json: Annotated[str,Field(min_length=2,max_length=60000)]
    risks: Annotated[list[Annotated[str,Field(max_length=1000)]],Field(min_length=1,max_length=20)]
    assumptions: Annotated[list[Annotated[str,Field(max_length=1000)]],Field(min_length=1,max_length=20)]
