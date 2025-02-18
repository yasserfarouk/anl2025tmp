from collections import defaultdict
from attr import asdict
from multiprocessing import cpu_count
from concurrent.futures import ProcessPoolExecutor, as_completed
from negmas.serialization import dump
from rich import print
from rich.progress import track
from collections.abc import Sequence
from typing import TypedDict
from pathlib import Path
from typing import Self
import random
from anl2025.ufun import CenterUFun
from negmas.helpers.types import get_class, get_full_type_name
from negmas.serialization import serialize, deserialize
from negmas.helpers.inout import load
from typing import Any
from anl2025.negotiator import ANL2025Negotiator
from anl2025.runner import (
    AssignedScenario,
    MultidealScenario,
    RunParams,
    SessionResults,
    assign_scenario,
    make_multideal_scenario,
)
from anl2025.common import TYPE_IDENTIFIER
from attr import define


class ScoreRecord(TypedDict):
    agent: str
    utility: float
    partner_average_utility: float
    scenario: str
    repetition: int
    rotation: int
    scenario_index: int
    index: int


@define
class JobInfo:
    assigned: AssignedScenario
    output: Path | None
    sname: str
    i: int
    j: int
    k: int
    center: type
    center_params: dict[str, Any] | None
    edges: tuple[type, ...] | list[type]
    edge_params: tuple[dict, ...] | list[dict]
    edge_info: list[tuple[type, dict[str, Any] | None]]
    nedges_counted: int


@define
class SessionInfo:
    """Information of a single negotiation during a tournament"""

    scenario_name: str
    repetition: int
    rotation: int
    center_type_name: str
    center_params: dict[str, Any]
    edge_type_names: list[str]
    edge_params: list[dict[str, Any] | None] | tuple[dict[str, Any] | None, ...]
    results: SessionResults
    path: Path | None = None


@define
class TournamentResults:
    """Results of a tournament"""

    final_scores: dict[str, float]
    scores: list[ScoreRecord]
    session_results: list[SessionInfo]


def run_session(job: JobInfo, dry: bool, verbose: bool) -> tuple[JobInfo, SessionInfo]:
    assigned = job.assigned
    output = job.output
    sname = job.sname
    i = job.i
    j = job.j
    center = job.center
    center_params = job.center_params
    edges = job.edges
    edge_params = job.edge_params
    r = assigned.run(
        output=output,
        name=f"{sname}_{j}_{i}",
        dry=dry,
        verbose=verbose,
    )
    return job, SessionInfo(
        scenario_name=sname,
        repetition=i,
        rotation=j,
        center_type_name=get_full_type_name(center),
        center_params=center_params if center_params else dict(),
        edge_type_names=[get_full_type_name(_) for _ in edges],
        edge_params=edge_params,  # type: ignore
        results=r,
    )


@define
class Tournament:
    competitors: tuple[str | type[ANL2025Negotiator], ...]
    scenarios: tuple[MultidealScenario, ...]
    run_params: RunParams
    competitor_params: tuple[dict[str, Any] | None, ...] | None = None

    @classmethod
    def from_scenarios(
        cls,
        competitors: Sequence[str | type[ANL2025Negotiator]],
        run_params: RunParams,
        scenarios: tuple[MultidealScenario, ...] = tuple(),
        n_generated: int = 0,
        nedges: int = 3,
        nissues: int = 3,
        nvalues: int = 7,
        # edge ufuns
        center_reserved_value_min: float = 0.0,
        center_reserved_value_max: float = 0.0,
        center_ufun_type: str | type[CenterUFun] = "MaxCenterUFun",
        center_ufun_params: dict[str, Any] | None = None,
        # edge ufuns
        edge_reserved_value_min: float = 0.1,
        edge_reserved_value_max: float = 0.4,
        competitor_params: tuple[dict[str, Any] | None, ...] | None = None,
    ) -> Self:
        if nedges > len(competitors):
            raise ValueError(
                f"We have {len(competitors)} competitors which is not enough for {nedges} edges"
            )
        return cls(
            competitors=tuple(competitors),
            competitor_params=competitor_params,
            run_params=run_params,
            scenarios=tuple(
                list(scenarios)
                + [
                    make_multideal_scenario(
                        nedges=nedges,
                        nissues=nissues,
                        nvalues=nvalues,
                        center_reserved_value_min=center_reserved_value_min,
                        center_reserved_value_max=center_reserved_value_max,
                        center_ufun_type=center_ufun_type,
                        center_ufun_params=center_ufun_params,
                        edge_reserved_value_min=edge_reserved_value_min,
                        edge_reserved_value_max=edge_reserved_value_max,
                    )
                    for _ in range(n_generated)
                ]
            ),
        )

    def __attrs_post_init__(self):
        if not self.competitor_params:
            self.competitor_params = tuple(dict() for _ in range(len(self.competitors)))
        self.competitor_params = tuple(
            dict() if not _ else _ for _ in self.competitor_params
        )

    def save(
        self,
        path: Path,
        separate_scenarios: bool = False,
        python_class_identifier=TYPE_IDENTIFIER,
    ):
        """
        Saves the tournament information.

        Args:
            path: A file to save information about the tournament to
            separate_scenarios: If `True`, scenarios will be saved inside a `scenarios` folder beside the path given otherwise they will be included in the file
        """
        data = dict(
            competitors=[get_full_type_name(_) for _ in self.competitors],
            run_params=asdict(self.run_params),
            competitor_params=None
            if not self.competitor_params
            else [
                serialize(_, python_class_identifier=python_class_identifier)
                for _ in self.competitor_params
            ],
        )
        if separate_scenarios:
            base = path.resolve().parent / "scenarios"
            for i, s in enumerate(self.scenarios):
                name = s.name if s.name else f"s{i:03}"
                dst = base
                dst.mkdir(parents=True, exist_ok=True)
                dump(
                    serialize(s, python_class_identifier=python_class_identifier),
                    dst / f"{name}.yaml",
                )
        else:
            data["scenarios"] = [
                serialize(_, python_class_identifier=python_class_identifier)
                for _ in self.scenarios
            ]
        dump(data, path)

    @classmethod
    def load(cls, path: Path, python_class_identifier=TYPE_IDENTIFIER):
        """Loads the tournament information"""
        info = load(path)
        base = path.resolve().parent / "scenarios"
        if "scenarios" not in info:
            info["scenarios"] = []
        else:
            info["scenarios"] = list(info["scenarios"])

        if base.exists():
            info["scenarios"] += [
                deserialize(f, python_class_identifier=python_class_identifier)
                for f in base.glob("*.yaml")
            ]

        return cls(
            competitors=info["competitors"],
            scenarios=[
                deserialize(_, python_class_identifier=python_class_identifier)
                for _ in info["scenarios"]
            ],  # type: ignore
            run_params=RunParams(**info["run_params"]),
            competitor_params=None  # type: ignore
            if not info.get("competitor_params", None)
            else deserialize(
                info["competitor_params"],
                python_class_identifier=python_class_identifier,
            ),
        )

    def run(
        self,
        n_repetitions: int,
        path: Path | None = None,
        verbose: bool = False,
        dry: bool = False,
        no_double_scores: bool = True,
        non_comptitor_types: tuple[str | type[ANL2025Negotiator], ...] | None = None,
        non_comptitor_params: tuple[dict[str, Any], ...] | None = None,
        n_jobs: int | float | None = 0,
        center_multiplier: float | None = None,
        edge_multiplier: float = 1,
    ) -> TournamentResults:
        """Run the tournament

        Args:
            n_repetitions: Number of repetitions of rotations over scenarios
            path: Path to save the results to
            verbose: Print progress
            dry: Do not really run the negotiations.
            no_double_scores: Avoid having the same agent in multiple positions in the same negotiation
            non_comptitor_types: Types to use to fill missing edge locations if not enough competitors are available
            non_comptitor_params: Paramters of non-competitor-types
            n_jobs: Number of parallel jobs to use.
                    None (and negative numbers) mean serially, 0 means use all cores, fractions mean fraction of available
                    cores, integers mean exact number of cores
            center_multiplier: A number to multiply center utilities with before calculating the score. Can be used
                               to give more or less value to being a center. If None, it will be equal to the number of edges.
            edge_multiplier: A number to multiply edge utilities with before calculating the score. Can be used
                               to give more or less value to being an edge

        Returns:
            `TournamentResults` with all scores and final-scores
        """
        if n_jobs is not None:
            if isinstance(n_jobs, float) and n_jobs < 1.0:
                n_jobs = int(0.5 + cpu_count() * n_jobs)
            elif isinstance(n_jobs, float):
                n_jobs = int(0.5 + n_jobs)
            if n_jobs < 0:
                n_jobs = None
            elif n_jobs == 0:
                n_jobs = cpu_count()

        results = []
        assert isinstance(self.competitor_params, tuple)
        final_scores = defaultdict(float)
        scores = []
        center_multiplier_val = center_multiplier

        def type_name(x):
            return get_full_type_name(x).replace("anl2025.negotiator.", "")

        if non_comptitor_types:
            non_comptitor_types = tuple(get_class(_) for _ in non_comptitor_types)
            non_comptitor_params = (
                non_comptitor_params
                if non_comptitor_params
                else tuple(dict() for _ in range(len(non_comptitor_types)))
            )
            non_competitors = [
                (n, p)
                for n, p in zip(non_comptitor_types, non_comptitor_params, strict=True)
            ]
        else:
            non_competitors = None

        jobs = []
        for i in track(range(n_repetitions), "Preparing Negotiation Sessions"):
            competitors = [
                (get_class(c), p)
                for c, p in zip(self.competitors, self.competitor_params, strict=True)
            ]
            for k, scenario in enumerate(self.scenarios):
                nedges = len(scenario.edge_ufuns)
                sname = scenario.name if scenario.name else f"s{k:03}"
                random.shuffle(competitors)
                for j in range(len(competitors)):
                    if len(competitors) >= nedges + 1:
                        players = competitors[: nedges + 1]
                    else:
                        # add extra players at the end if not enough competitors are available
                        players = competitors + list(
                            random.choices(
                                non_competitors if non_competitors else competitors,
                                k=nedges + 1 - len(competitors),
                            )
                        )
                    # ignore the randomly added edges if no-double-scores is set
                    nedges_counted = (
                        nedges
                        if not no_double_scores
                        else min(len(competitors) - 1, nedges)
                    )
                    if path:
                        output = path / "results" / sname / f"r{j:03}t{i:03}"
                    else:
                        output = None
                    center, center_params = players[j]
                    edge_info = [_ for _ in players[:j] + players[j + 1 :]]
                    # not sure if the following shuffle is useful!
                    # It tries to randomize the order of the edges to avoid
                    # having a systematic bias but we randomize competitors anyway.
                    random.shuffle(edge_info)
                    edges = [_[0] for _ in edge_info]
                    edge_params = [_[1] if _[1] else dict() for _ in edge_info]
                    assigned = assign_scenario(
                        scenario=scenario,
                        run_params=self.run_params,
                        center_type=center,
                        center_params=center_params,
                        edge_types=edges,  # type: ignore
                        edge_params=edge_params,
                        verbose=verbose,
                        sample_edges=False,
                    )
                    jobs.append(
                        JobInfo(
                            assigned,
                            output,
                            sname,
                            i,
                            j,
                            k,
                            center,
                            center_params,
                            edges,
                            edge_params,
                            edge_info,
                            nedges_counted,
                        )
                    )
            # This rotation guarantees that every competitor is
            # the center once per scenario per repetition
            competitors = [competitors[-1]] + competitors[:-1]
        if verbose:
            print(f"Will run {len(jobs)} negotiations")

        def process_info(job: JobInfo, info: SessionInfo):
            center_multiplier = (
                center_multiplier_val
                if center_multiplier_val is not None
                else len(job.edge_info)
            )
            r = info.results
            results.append(info)
            center, center_params = job.center, job.center_params
            cname = (
                type_name(center)
                if not center_params
                else f"{type_name(center)}_{hash(str(center_params))}"
            )
            mean_edge_utility = sum(r.edge_utilities) / len(r.edge_utilities)
            scores.append(
                dict(
                    agent=cname,
                    utility=r.center_utility * center_multiplier,
                    partner_average_utility=mean_edge_utility,
                    scenario=job.sname,
                    repetition=job.i,
                    rotation=job.j,
                    scenario_index=job.k,
                    index=0,
                )
            )
            final_scores[cname] += r.center_utility
            for e, (c, p) in enumerate(job.edge_info[: job.nedges_counted]):
                cname = type_name(c) if not p else f"{type_name(c)}_{hash(str(p))}"
                scores.append(
                    dict(
                        agent=cname,
                        utility=r.edge_utilities[e] * edge_multiplier,
                        partner_average_utility=r.center_utility,
                        scenario=job.sname,
                        repetition=job.i,
                        rotation=job.j,
                        scenario_index=job.k,
                        index=e + 1,
                    )
                )
                final_scores[cname] += r.edge_utilities[e]
            if verbose:
                print(f"Center Utility: {r.center_utility}")
                print(f"Edge Utilities: {r.edge_utilities}")

        if n_jobs is None:
            for job in track(jobs, "Running Negotiations"):
                job, info = run_session(job, dry, verbose)
                process_info(job, info)
        else:
            assert n_jobs > 0
            with ProcessPoolExecutor(max_workers=n_jobs) as executor:
                # Submit all jobs and store the futures
                futures = [
                    executor.submit(run_session, job, dry, verbose) for job in jobs
                ]

                # Process results as they become available
                for future in as_completed(futures):
                    try:
                        job, info = future.result()
                        process_info(job, info)
                    except Exception as e:
                        print(f"Job failed with exception: {e}")

        return TournamentResults(
            final_scores={k: v for k, v in final_scores.items()},
            scores=scores,
            session_results=results,
        )
