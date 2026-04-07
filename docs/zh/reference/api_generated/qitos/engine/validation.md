# `qitos.engine.validation`

- 模块分组: `qitos.engine`
- 源码: [qitos/engine/validation.py](https://github.com/Qitor/qitos/blob/main/qitos/engine/validation.py)

## 快速跳转

- [类](#classes)
- [函数](#functions)
- [Class: `StateValidationGate`](#class-statevalidationgate)
- [Class: `StateValidatorChain`](#class-statevalidatorchain)
- [Function: `validate_final_consistency`](#function-validate-final-consistency)
- [Function: `validate_optional_plan_fields`](#function-validate-optional-plan-fields)
- [Function: `validate_step_bounds`](#function-validate-step-bounds)

## Classes

<a id="class-statevalidationgate"></a>
???+ note "Class: `StateValidationGate(self, validators: 'Iterable[Validator]' = [<function validate_step_bounds at 0x102f67d80>, <function validate_optional_plan_fields at 0x102f84400>, <function validate_final_consistency at 0x102f844a0>])`"
    Run validation checks before and after each engine phase.

<a id="class-statevalidatorchain"></a>
???+ note "Class: `StateValidatorChain(self, validators: 'List[Validator]') -> None`"
    StateValidatorChain(validators: 'List[Validator]')

## Functions

<a id="function-validate-final-consistency"></a>
???+ note "Function: `validate_final_consistency(state: 'StateSchema') -> 'None'`"
    _No summary available._

<a id="function-validate-optional-plan-fields"></a>
???+ note "Function: `validate_optional_plan_fields(state: 'StateSchema') -> 'None'`"
    _No summary available._

<a id="function-validate-step-bounds"></a>
???+ note "Function: `validate_step_bounds(state: 'StateSchema') -> 'None'`"
    _No summary available._

## Source Index

- [qitos/engine/validation.py](https://github.com/Qitor/qitos/blob/main/qitos/engine/validation.py)