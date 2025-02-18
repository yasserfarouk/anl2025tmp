from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Sequence, Callable
from pathlib import Path
from typing import TypeVar
from negmas.preferences import BaseUtilityFunction
from negmas.preferences import UtilityFunction
from negmas.outcomes import (
    CartesianOutcomeSpace,
    EnumeratingOutcomeSpace,
    Outcome,
    OutcomeSpace,
    make_os,
    make_issue,
)
from negmas.warnings import warn
import numpy as np

TRACE_COLS = (
    "time",
    "relative_time",
    "step",
    "negotiator",
    "offer",
    "responses",
    "state",
)

__all__ = [
    "convert_to_center_ufun",
    "flatten_outcome_spaces",
    "unflatten_outcome_space",
    "CenterUFun",
    "FlatCenterUFun",
    "LambdaCenterUFun",
    "LambdaUtilityFunction",
    "MaxCenterUFun",
    "MeanSMCenterUFun",
    "SideUFun",
    "SingleAgreementSideUFunMixin",
    "UtilityCombiningCenterUFun",
]

TUFun = TypeVar("TUFun", bound=UtilityFunction)
CenterEvaluator = Callable[[tuple[Outcome | None, ...] | None], float]
EdgeEvaluator = Callable[[Outcome | None], float]


def unflatten_outcome_space(
    outcome_space: CartesianOutcomeSpace, nissues: tuple[int, ...] | list[int]
) -> tuple[CartesianOutcomeSpace, ...]:
    """Distributes the issues of an outcome-space into a tuple of outcome-spaces."""
    nissues = list(nissues)
    beg = [0] + nissues[:-1]
    end = nissues
    return tuple(
        make_os(outcome_space.issues[i:j], name=f"OS{i}")
        for i, j in zip(beg, end, strict=True)
    )


def convert_to_center_ufun(
    ufun: UtilityFunction,
    nissues: tuple[int],
    side_evaluators: list[EdgeEvaluator] | None = None,
) -> "CenterUFun":
    """Creates a center ufun from any standard ufun with ufuns side ufuns"""
    assert ufun.outcome_space and isinstance(ufun.outcome_space, CartesianOutcomeSpace)
    evaluator = ufun
    if side_evaluators is not None:
        return LambdaCenterUFunWithSides(
            outcome_spaces=unflatten_outcome_space(ufun.outcome_space, nissues),
            evaluator=evaluator,
            side_evaluators=tuple(side_evaluators),
        )
    return LambdaCenterUFun(
        outcome_spaces=unflatten_outcome_space(ufun.outcome_space, nissues),
        evaluator=evaluator,
    )


def flatten_outcome_spaces(
    outcome_spaces: tuple[OutcomeSpace, ...],
    add_index_to_issue_names: bool = False,
    add_os_to_issue_name: bool = False,
) -> tuple[CartesianOutcomeSpace, tuple[int, ...]]:
    """Generates a single outcome-space which is the Cartesian product of input outcome_spaces."""

    def _name(i: int, os_name: str | None, issue_name: str | None) -> str:
        x = issue_name if issue_name else ""
        if add_os_to_issue_name and os_name:
            x = f"{os_name}:{x}"
        if add_index_to_issue_names:
            x = f"{x}:{i}"
        return x

    values, names, nissues = [], [], []
    for i, os in enumerate(outcome_spaces):
        if isinstance(os, EnumeratingOutcomeSpace):
            values.append(list(os.enumerate()))
            names.append(_name(i, "", os.name))
            nissues.append(1)
        elif isinstance(os, CartesianOutcomeSpace):
            for issue in os.issues:
                values.append(issue.values)
                names.append(_name(i, os.name, issue.name))
            nissues.append(len(os.issues))
        else:
            raise TypeError(
                f"Outcome space of type {type(os)} cannot be combined with other outcome-spaces"
            )
    return make_os([make_issue(v, n) for v, n in zip(values, names)]), tuple(nissues)


class CenterUFun(UtilityFunction, ABC):
    """
    Base class of center utility functions.

    Remarks:
        - Can be constructed by either passing a single `outcome_space` and `n_edges` or a tuple of `outcome_spaces`
        - It's eval() method  receives a tuple of negotiation results and returns a float
    """

    def __init__(
        self,
        *args,
        outcome_spaces: tuple[OutcomeSpace, ...] = (),
        n_edges: int = 0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if not outcome_spaces and self.outcome_space:
            outcome_spaces = tuple([self.outcome_space] * n_edges)
        self._outcome_spaces = outcome_spaces
        self.n_edges = len(outcome_spaces)
        self.__kwargs = dict(
            reserved_value=self.reserved_value,
            owner=self.owner,
            invalid_value=self._invalid_value,
            name=self.name,
            id=self.id,
        )
        try:
            self.outcome_space, self.__nissues = flatten_outcome_spaces(
                outcome_spaces, add_index_to_issue_names=True, add_os_to_issue_name=True
            )
        except Exception:
            warn("Failed to find the Cartesian product of input outcome spaces")
            self.outcome_space, self.__nissues = None, tuple()

    @property
    def outcome_spaces(self) -> tuple[OutcomeSpace, ...]:
        return self._outcome_spaces

    def flatten(
        self,
        add_index_to_issue_names: bool = False,
        add_os_to_issue_name: bool = False,
    ) -> "FlatCenterUFun":
        os = flatten_outcome_spaces(
            self._outcome_spaces, add_index_to_issue_names, add_os_to_issue_name
        )
        return FlatCenterUFun(
            base_ufun=self, nissues=self.__nissues, outcome_space=os, **self.__kwargs
        )

    @abstractmethod
    def eval(self, offer: tuple[Outcome | None, ...] | None) -> float:
        """
        Evaluates the utility of a given set of offers.

        Remarks:
            - Order matters: The order of outcomes in the offer is stable over all calls.
            - A missing offer is represented by `None`
        """

    def side_ufuns(self, n_edges: int) -> tuple[BaseUtilityFunction | None, ...]:
        """Should return an independent ufun for each side negotiator of the center."""
        return tuple(
            SideUFun(center_ufun=self, n_edges=self.n_edges, index=i)
            for i in range(n_edges)
        )


class LambdaCenterUFun(CenterUFun):
    """
    A center utility function that implements an arbitrary evaluator
    """

    def __init__(self, *args, evaluator: CenterEvaluator, **kwargs):
        super().__init__(*args, **kwargs)
        self._eval = evaluator

    def eval(self, offer: tuple[Outcome | None, ...] | None) -> float:
        return self._eval(offer)

    @classmethod
    def load(cls, folder: Path):
        pass


class LambdaUtilityFunction(UtilityFunction):
    """A utility function that implements an arbitrary mapping"""

    def __init__(self, *args, evaluator: EdgeEvaluator, **kwargs):
        super().__init__(*args, **kwargs)
        self._evaluator = evaluator

    def __call__(self, offer: Outcome | None) -> float:
        return self._evaluator(offer)

    def eval(self, offer: Outcome) -> float:
        return self._evaluator(offer)


class LambdaCenterUFunWithSides(CenterUFun):
    """
    A center utility function that implements an arbitrary evaluator
    """

    def __init__(
        self,
        *args,
        evaluator: CenterEvaluator,
        side_evaluators: EdgeEvaluator | Sequence[EdgeEvaluator] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._eval = evaluator
        if not side_evaluators:
            self._side_ufuns = None
            return
        if not isinstance(side_evaluators, Sequence):
            evaluators = [side_evaluators] * len(self._outcome_spaces)
        else:
            evaluators = list(side_evaluators)
        sides: list[UtilityFunction] = []
        for e, o in zip(evaluators, self._outcome_spaces):
            sides.append(LambdaUtilityFunction(outcome_space=o, evaluator=e))
        self._side_ufuns = tuple(sides)

    def eval(self, offer: tuple[Outcome | None, ...] | None) -> float:
        return self._eval(offer)

    def side_ufuns(self, n_edges: int) -> tuple[BaseUtilityFunction | None, ...]:
        if self._side_ufuns is None:
            return super().side_ufuns(n_edges)
        return self._side_ufuns


class FlatCenterUFun(UtilityFunction):
    """
    A flattened version of a center ufun.

    A normal CenterUFun takes outcomes as a tuple of outcomes (one for each edge).
    A flattened version of the same ufun takes input as just a single outcome containing
    a concatenation of the outcomes in all edges.

    Example:

        ```python
        x = CenterUFun(...)
        y = x.flatten()

        x(((1, 0.5), (3, true), (7,))) == y((1, 0.5, 3 , true, 7))
        ```
    """

    def __init__(
        self, *args, base_ufun: CenterUFun, nissues: tuple[int, ...], **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.__base = base_ufun
        self.__nissues = list(nissues)

    def _unflatten(self, outcome: Outcome) -> tuple[Outcome, ...]:
        beg = [0] + self.__nissues[:-1]
        end = self.__nissues
        outcomes = []
        for i, j in zip(beg, end, strict=True):
            outcomes.append(tuple(outcome[i:j]))
        return tuple(outcomes)

    def eval(self, offer: Outcome) -> float:
        return self.__base.eval(self._unflatten(offer))


class UtilityCombiningCenterUFun(CenterUFun):
    """
    A center ufun with a side-ufun defined for each thread.

    The utility of the center is a function of the ufuns of the edges.
    """

    def __init__(self, *args, ufuns: tuple[BaseUtilityFunction, ...], **kwargs):
        super().__init__(*args, **kwargs)
        self._ufuns = ufuns

    @abstractmethod
    def combine(self, values: Sequence[float]) -> float:
        """Combines the utilities of all negotiation  threads into a single value"""

    def eval(self, offer: tuple[Outcome | None, ...] | None) -> float:
        if not offer:
            return self.reserved_value
        return self.combine(tuple(float(u(_)) for u, _ in zip(self._ufuns, offer)))

    def side_ufuns(self, n_edges: int) -> tuple[BaseUtilityFunction, ...]:
        assert (
            n_edges == len(self._ufuns)
        ), f"Initialized with {len(self._ufuns)} ufuns but you are asking for ufuns for {n_edges} side negotiators."
        return self._ufuns


class MaxCenterUFun(UtilityCombiningCenterUFun):
    """
    The max center ufun.

    The utility of the center is the maximum of the utilities it got in each negotiation (called side utilities)
    """

    def combine(self, values: Sequence[float]) -> float:
        return max(values)


class SideUFun(BaseUtilityFunction):
    """
    Side ufun corresponding to the i's component of a center ufun.
    """

    def __init__(
        self, *args, center_ufun: CenterUFun, index: int, n_edges: int, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.__center_ufun = center_ufun
        self.__index = index
        self.__n_edges = n_edges

    def eval(self, offer: Outcome | None) -> float:
        offers: list[Outcome | None] = [None] * self.__n_edges
        offers[self.__index] = offer
        return self.__center_ufun(tuple(offers))


class SingleAgreementSideUFunMixin:
    """Can be mixed with any CenterUFun that is not a combining ufun to create side_ufuns that assume failure on all other negotiations.

    See Also:
        `MeanSMCenterUFun`
    """

    def side_ufuns(self, n_edges: int) -> tuple[BaseUtilityFunction, ...]:
        """Should return an independent ufun for each side negotiator of the center"""
        return tuple(
            SideUFun(center_ufun=self, n_edges=n_edges, index=i)  # type: ignore
            for i in range(n_edges)
        )


class MeanSMCenterUFun(SingleAgreementSideUFunMixin, CenterUFun):
    """A ufun that just  returns the average mean+std dev. in each issue of the agreements as the utility value"""

    def eval(self, offer: tuple[Outcome | None, ...] | None) -> float:
        if not offer:
            return 0.0
        n_edges = len(offer)
        if n_edges < 2:
            return 0.1
        vals = defaultdict(lambda: [0.0] * n_edges)
        for e, outcome in enumerate(offer):
            if not outcome:
                continue
            for i, v in enumerate(outcome):
                try:
                    vals[e][i] = float(v[1:])
                except Exception:
                    pass

        return float(sum(np.mean(x) + np.std(x) for x in vals.values())) / len(vals)
