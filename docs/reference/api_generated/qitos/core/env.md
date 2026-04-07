# `qitos.core.env`

- Module Group: `qitos.core`
- Source: [qitos/core/env.py](https://github.com/Qitor/qitos/blob/main/qitos/core/env.py)

## Quick Jump

- [Classes](#classes)
- [Functions](#functions)
- [Class: `CommandCapability`](#class-commandcapability)
- [Class: `Env`](#class-env)
- [Class: `EnvObservation`](#class-envobservation)
- [Class: `EnvSpec`](#class-envspec)
- [Class: `EnvStepResult`](#class-envstepresult)
- [Class: `FileSystemCapability`](#class-filesystemcapability)
- [Class: `TerminalCapability`](#class-terminalcapability)

## Classes

<a id="class-commandcapability"></a>
???+ note "Class: `CommandCapability(self, /, *args, **kwargs)`"
    Command execution capability contract used by env implementations.

<a id="class-env"></a>
???+ note "Class: `Env(self, /, *args, **kwargs)`"
    Canonical environment interface for agent-world interaction.

<a id="class-envobservation"></a>
???+ note "Class: `EnvObservation(self, data: 'Dict[str, Any]' = <factory>, metadata: 'Dict[str, Any]' = <factory>) -> None`"
    Structured environment observation payload.

<a id="class-envspec"></a>
???+ note "Class: `EnvSpec(self, type: 'str', config: 'Dict[str, Any]' = <factory>, required_tools: 'List[str]' = <factory>, capabilities: 'List[str]' = <factory>, metadata: 'Dict[str, Any]' = <factory>) -> None`"
    Declarative environment requirement attached to a task.

<a id="class-envstepresult"></a>
???+ note "Class: `EnvStepResult(self, observation: 'EnvObservation' = <factory>, done: 'bool' = False, reward: 'Optional[float]' = None, info: 'Dict[str, Any]' = <factory>, error: 'Optional[str]' = None) -> None`"
    Structured result emitted by one environment step.

<a id="class-filesystemcapability"></a>
???+ note "Class: `FileSystemCapability(self, /, *args, **kwargs)`"
    Filesystem capability contract used by env implementations.

<a id="class-terminalcapability"></a>
???+ note "Class: `TerminalCapability(self, /, *args, **kwargs)`"
    Interactive terminal capability contract used by env implementations.

## Functions

- _None_

## Source Index

- [qitos/core/env.py](https://github.com/Qitor/qitos/blob/main/qitos/core/env.py)