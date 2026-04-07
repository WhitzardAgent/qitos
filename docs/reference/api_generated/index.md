# API Reference (Auto-Generated)

This section is generated automatically at build time from the `qitos` package.

## Group Jump

- [qitos.benchmark](#group-qitos-benchmark)
- [qitos.cli](#group-qitos-cli)
- [qitos.core](#group-qitos-core)
- [qitos.debug](#group-qitos-debug)
- [qitos.engine](#group-qitos-engine)
- [qitos.evaluate](#group-qitos-evaluate)
- [qitos.kit](#group-qitos-kit)
- [qitos.metric](#group-qitos-metric)
- [qitos.models](#group-qitos-models)
- [qitos.qita](#group-qitos-qita)
- [qitos.render](#group-qitos-render)
- [qitos.trace](#group-qitos-trace)

## Modules by Group

<a id="group-qitos-benchmark"></a>
### `qitos.benchmark`

- [`qitos.benchmark`](qitos/benchmark.md)
- [`qitos.benchmark.base`](qitos/benchmark/base.md)
- [`qitos.benchmark.cybench`](qitos/benchmark/cybench.md)
- [`qitos.benchmark.cybench.adapter`](qitos/benchmark/cybench/adapter.md)
- [`qitos.benchmark.cybench.runtime`](qitos/benchmark/cybench/runtime.md)
- [`qitos.benchmark.gaia`](qitos/benchmark/gaia.md)
- [`qitos.benchmark.gaia.adapter`](qitos/benchmark/gaia/adapter.md)
- [`qitos.benchmark.tau_bench`](qitos/benchmark/tau_bench.md)
- [`qitos.benchmark.tau_bench.adapter`](qitos/benchmark/tau_bench/adapter.md)
- [`qitos.benchmark.tau_bench.port`](qitos/benchmark/tau_bench/port.md)
- [`qitos.benchmark.tau_bench.port.envs`](qitos/benchmark/tau_bench/port/envs.md)
- [`qitos.benchmark.tau_bench.port.envs.airline`](qitos/benchmark/tau_bench/port/envs/airline.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.data`](qitos/benchmark/tau_bench/port/envs/airline/data.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.env`](qitos/benchmark/tau_bench/port/envs/airline/env.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.rules`](qitos/benchmark/tau_bench/port/envs/airline/rules.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tasks`](qitos/benchmark/tau_bench/port/envs/airline/tasks.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tasks_test`](qitos/benchmark/tau_bench/port/envs/airline/tasks_test.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools`](qitos/benchmark/tau_bench/port/envs/airline/tools.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.book_reservation`](qitos/benchmark/tau_bench/port/envs/airline/tools/book_reservation.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.calculate`](qitos/benchmark/tau_bench/port/envs/airline/tools/calculate.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.cancel_reservation`](qitos/benchmark/tau_bench/port/envs/airline/tools/cancel_reservation.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.get_reservation_details`](qitos/benchmark/tau_bench/port/envs/airline/tools/get_reservation_details.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.get_user_details`](qitos/benchmark/tau_bench/port/envs/airline/tools/get_user_details.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.list_all_airports`](qitos/benchmark/tau_bench/port/envs/airline/tools/list_all_airports.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.search_direct_flight`](qitos/benchmark/tau_bench/port/envs/airline/tools/search_direct_flight.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.search_onestop_flight`](qitos/benchmark/tau_bench/port/envs/airline/tools/search_onestop_flight.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.send_certificate`](qitos/benchmark/tau_bench/port/envs/airline/tools/send_certificate.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.think`](qitos/benchmark/tau_bench/port/envs/airline/tools/think.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.transfer_to_human_agents`](qitos/benchmark/tau_bench/port/envs/airline/tools/transfer_to_human_agents.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.update_reservation_baggages`](qitos/benchmark/tau_bench/port/envs/airline/tools/update_reservation_baggages.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.update_reservation_flights`](qitos/benchmark/tau_bench/port/envs/airline/tools/update_reservation_flights.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.tools.update_reservation_passengers`](qitos/benchmark/tau_bench/port/envs/airline/tools/update_reservation_passengers.md)
- [`qitos.benchmark.tau_bench.port.envs.airline.wiki`](qitos/benchmark/tau_bench/port/envs/airline/wiki.md)
- [`qitos.benchmark.tau_bench.port.envs.retail`](qitos/benchmark/tau_bench/port/envs/retail.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.data`](qitos/benchmark/tau_bench/port/envs/retail/data.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.env`](qitos/benchmark/tau_bench/port/envs/retail/env.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.rules`](qitos/benchmark/tau_bench/port/envs/retail/rules.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tasks`](qitos/benchmark/tau_bench/port/envs/retail/tasks.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tasks_dev`](qitos/benchmark/tau_bench/port/envs/retail/tasks_dev.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tasks_test`](qitos/benchmark/tau_bench/port/envs/retail/tasks_test.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tasks_train`](qitos/benchmark/tau_bench/port/envs/retail/tasks_train.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools`](qitos/benchmark/tau_bench/port/envs/retail/tools.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.calculate`](qitos/benchmark/tau_bench/port/envs/retail/tools/calculate.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.cancel_pending_order`](qitos/benchmark/tau_bench/port/envs/retail/tools/cancel_pending_order.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.exchange_delivered_order_items`](qitos/benchmark/tau_bench/port/envs/retail/tools/exchange_delivered_order_items.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.find_user_id_by_email`](qitos/benchmark/tau_bench/port/envs/retail/tools/find_user_id_by_email.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.find_user_id_by_name_zip`](qitos/benchmark/tau_bench/port/envs/retail/tools/find_user_id_by_name_zip.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.get_order_details`](qitos/benchmark/tau_bench/port/envs/retail/tools/get_order_details.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.get_product_details`](qitos/benchmark/tau_bench/port/envs/retail/tools/get_product_details.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.get_user_details`](qitos/benchmark/tau_bench/port/envs/retail/tools/get_user_details.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.list_all_product_types`](qitos/benchmark/tau_bench/port/envs/retail/tools/list_all_product_types.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.modify_pending_order_address`](qitos/benchmark/tau_bench/port/envs/retail/tools/modify_pending_order_address.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.modify_pending_order_items`](qitos/benchmark/tau_bench/port/envs/retail/tools/modify_pending_order_items.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.modify_pending_order_payment`](qitos/benchmark/tau_bench/port/envs/retail/tools/modify_pending_order_payment.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.modify_user_address`](qitos/benchmark/tau_bench/port/envs/retail/tools/modify_user_address.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.return_delivered_order_items`](qitos/benchmark/tau_bench/port/envs/retail/tools/return_delivered_order_items.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.think`](qitos/benchmark/tau_bench/port/envs/retail/tools/think.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.tools.transfer_to_human_agents`](qitos/benchmark/tau_bench/port/envs/retail/tools/transfer_to_human_agents.md)
- [`qitos.benchmark.tau_bench.port.envs.retail.wiki`](qitos/benchmark/tau_bench/port/envs/retail/wiki.md)
- [`qitos.benchmark.tau_bench.port.envs.tool`](qitos/benchmark/tau_bench/port/envs/tool.md)
- [`qitos.benchmark.tau_bench.port.types`](qitos/benchmark/tau_bench/port/types.md)
- [`qitos.benchmark.tau_bench.runtime`](qitos/benchmark/tau_bench/runtime.md)

<a id="group-qitos-cli"></a>
### `qitos.cli`

- [`qitos.cli`](qitos/cli.md)

<a id="group-qitos-core"></a>
### `qitos.core`

- [`qitos.core`](qitos/core.md)
- [`qitos.core.action`](qitos/core/action.md)
- [`qitos.core.agent_module`](qitos/core/agent_module.md)
- [`qitos.core.decision`](qitos/core/decision.md)
- [`qitos.core.env`](qitos/core/env.md)
- [`qitos.core.errors`](qitos/core/errors.md)
- [`qitos.core.history`](qitos/core/history.md)
- [`qitos.core.memory`](qitos/core/memory.md)
- [`qitos.core.state`](qitos/core/state.md)
- [`qitos.core.task`](qitos/core/task.md)
- [`qitos.core.tool`](qitos/core/tool.md)
- [`qitos.core.tool_registry`](qitos/core/tool_registry.md)

<a id="group-qitos-debug"></a>
### `qitos.debug`

- [`qitos.debug`](qitos/debug.md)
- [`qitos.debug.breakpoints`](qitos/debug/breakpoints.md)
- [`qitos.debug.inspector`](qitos/debug/inspector.md)
- [`qitos.debug.replay`](qitos/debug/replay.md)

<a id="group-qitos-engine"></a>
### `qitos.engine`

- [`qitos.engine`](qitos/engine.md)
- [`qitos.engine._action_runtime`](qitos/engine/_action_runtime.md)
- [`qitos.engine._control_runtime`](qitos/engine/_control_runtime.md)
- [`qitos.engine._env_runtime`](qitos/engine/_env_runtime.md)
- [`qitos.engine._model_runtime`](qitos/engine/_model_runtime.md)
- [`qitos.engine._trace_runtime`](qitos/engine/_trace_runtime.md)
- [`qitos.engine.action_executor`](qitos/engine/action_executor.md)
- [`qitos.engine.branching`](qitos/engine/branching.md)
- [`qitos.engine.critic`](qitos/engine/critic.md)
- [`qitos.engine.engine`](qitos/engine/engine.md)
- [`qitos.engine.hooks`](qitos/engine/hooks.md)
- [`qitos.engine.parser`](qitos/engine/parser.md)
- [`qitos.engine.recovery`](qitos/engine/recovery.md)
- [`qitos.engine.search`](qitos/engine/search.md)
- [`qitos.engine.states`](qitos/engine/states.md)
- [`qitos.engine.stop_criteria`](qitos/engine/stop_criteria.md)
- [`qitos.engine.validation`](qitos/engine/validation.md)

<a id="group-qitos-evaluate"></a>
### `qitos.evaluate`

- [`qitos.evaluate`](qitos/evaluate.md)
- [`qitos.evaluate.base`](qitos/evaluate/base.md)

<a id="group-qitos-kit"></a>
### `qitos.kit`

- [`qitos.kit`](qitos/kit.md)
- [`qitos.kit.critic`](qitos/kit/critic.md)
- [`qitos.kit.critic.pass_through`](qitos/kit/critic/pass_through.md)
- [`qitos.kit.critic.react_self_reflection`](qitos/kit/critic/react_self_reflection.md)
- [`qitos.kit.critic.self_reflection`](qitos/kit/critic/self_reflection.md)
- [`qitos.kit.env`](qitos/kit/env.md)
- [`qitos.kit.env.docker_env`](qitos/kit/env/docker_env.md)
- [`qitos.kit.env.host_env`](qitos/kit/env/host_env.md)
- [`qitos.kit.env.repo_env`](qitos/kit/env/repo_env.md)
- [`qitos.kit.env.text_web_env`](qitos/kit/env/text_web_env.md)
- [`qitos.kit.env.tmux_env`](qitos/kit/env/tmux_env.md)
- [`qitos.kit.evaluate`](qitos/kit/evaluate.md)
- [`qitos.kit.evaluate.cybench`](qitos/kit/evaluate/cybench.md)
- [`qitos.kit.evaluate.dsl_based`](qitos/kit/evaluate/dsl_based.md)
- [`qitos.kit.evaluate.model_based`](qitos/kit/evaluate/model_based.md)
- [`qitos.kit.evaluate.rule_based`](qitos/kit/evaluate/rule_based.md)
- [`qitos.kit.history`](qitos/kit/history.md)
- [`qitos.kit.history.compact_history`](qitos/kit/history/compact_history.md)
- [`qitos.kit.history.token_budget_history`](qitos/kit/history/token_budget_history.md)
- [`qitos.kit.history.window_history`](qitos/kit/history/window_history.md)
- [`qitos.kit.memory`](qitos/kit/memory.md)
- [`qitos.kit.memory.markdown_file_memory`](qitos/kit/memory/markdown_file_memory.md)
- [`qitos.kit.memory.summary_memory`](qitos/kit/memory/summary_memory.md)
- [`qitos.kit.memory.vector_memory`](qitos/kit/memory/vector_memory.md)
- [`qitos.kit.memory.window_memory`](qitos/kit/memory/window_memory.md)
- [`qitos.kit.metric`](qitos/kit/metric.md)
- [`qitos.kit.metric.basic`](qitos/kit/metric/basic.md)
- [`qitos.kit.metric.cybench`](qitos/kit/metric/cybench.md)
- [`qitos.kit.metric.reward`](qitos/kit/metric/reward.md)
- [`qitos.kit.parser`](qitos/kit/parser.md)
- [`qitos.kit.parser.func_parser`](qitos/kit/parser/func_parser.md)
- [`qitos.kit.parser.json_parser`](qitos/kit/parser/json_parser.md)
- [`qitos.kit.parser.parser_utils`](qitos/kit/parser/parser_utils.md)
- [`qitos.kit.parser.react_parser`](qitos/kit/parser/react_parser.md)
- [`qitos.kit.parser.terminus_json_parser`](qitos/kit/parser/terminus_json_parser.md)
- [`qitos.kit.parser.terminus_xml_parser`](qitos/kit/parser/terminus_xml_parser.md)
- [`qitos.kit.parser.xml_parser`](qitos/kit/parser/xml_parser.md)
- [`qitos.kit.planning`](qitos/kit/planning.md)
- [`qitos.kit.planning.agent_blocks`](qitos/kit/planning/agent_blocks.md)
- [`qitos.kit.planning.dynamic_tree_search`](qitos/kit/planning/dynamic_tree_search.md)
- [`qitos.kit.planning.plan`](qitos/kit/planning/plan.md)
- [`qitos.kit.planning.search`](qitos/kit/planning/search.md)
- [`qitos.kit.planning.state_ops`](qitos/kit/planning/state_ops.md)
- [`qitos.kit.prompts`](qitos/kit/prompts.md)
- [`qitos.kit.prompts.template`](qitos/kit/prompts/template.md)
- [`qitos.kit.skill`](qitos/kit/skill.md)
- [`qitos.kit.skill.cli`](qitos/kit/skill/cli.md)
- [`qitos.kit.skill.injector`](qitos/kit/skill/injector.md)
- [`qitos.kit.skill.integration`](qitos/kit/skill/integration.md)
- [`qitos.kit.skill.loader`](qitos/kit/skill/loader.md)
- [`qitos.kit.skill.manager`](qitos/kit/skill/manager.md)
- [`qitos.kit.skill.manifest`](qitos/kit/skill/manifest.md)
- [`qitos.kit.skill.provider`](qitos/kit/skill/provider.md)
- [`qitos.kit.skill.registry`](qitos/kit/skill/registry.md)
- [`qitos.kit.state`](qitos/kit/state.md)
- [`qitos.kit.state.plan`](qitos/kit/state/plan.md)
- [`qitos.kit.tool`](qitos/kit/tool.md)
- [`qitos.kit.tool.codebase`](qitos/kit/tool/codebase.md)
- [`qitos.kit.tool.coding`](qitos/kit/tool/coding.md)
- [`qitos.kit.tool.cybench`](qitos/kit/tool/cybench.md)
- [`qitos.kit.tool.editor`](qitos/kit/tool/editor.md)
- [`qitos.kit.tool.epub`](qitos/kit/tool/epub.md)
- [`qitos.kit.tool.file`](qitos/kit/tool/file.md)
- [`qitos.kit.tool.library`](qitos/kit/tool/library.md)
- [`qitos.kit.tool.library.base`](qitos/kit/tool/library/base.md)
- [`qitos.kit.tool.library.store`](qitos/kit/tool/library/store.md)
- [`qitos.kit.tool.notebook`](qitos/kit/tool/notebook.md)
- [`qitos.kit.tool.shell`](qitos/kit/tool/shell.md)
- [`qitos.kit.tool.skill_tools`](qitos/kit/tool/skill_tools.md)
- [`qitos.kit.tool.taskboard`](qitos/kit/tool/taskboard.md)
- [`qitos.kit.tool.terminal`](qitos/kit/tool/terminal.md)
- [`qitos.kit.tool.text_web_browser`](qitos/kit/tool/text_web_browser.md)
- [`qitos.kit.tool.thinking`](qitos/kit/tool/thinking.md)
- [`qitos.kit.tool.tools`](qitos/kit/tool/tools.md)
- [`qitos.kit.tool.toolset`](qitos/kit/tool/toolset.md)
- [`qitos.kit.tool.web`](qitos/kit/tool/web.md)

<a id="group-qitos-metric"></a>
### `qitos.metric`

- [`qitos.metric`](qitos/metric.md)
- [`qitos.metric.base`](qitos/metric/base.md)

<a id="group-qitos-models"></a>
### `qitos.models`

- [`qitos.models`](qitos/models.md)
- [`qitos.models.base`](qitos/models/base.md)
- [`qitos.models.local`](qitos/models/local.md)
- [`qitos.models.openai`](qitos/models/openai.md)

<a id="group-qitos-qita"></a>
### `qitos.qita`

- [`qitos.qita`](qitos/qita.md)
- [`qitos.qita.cli`](qitos/qita/cli.md)

<a id="group-qitos-render"></a>
### `qitos.render`

- [`qitos.render`](qitos/render.md)
- [`qitos.render.cli_render`](qitos/render/cli_render.md)
- [`qitos.render.content_renderer`](qitos/render/content_renderer.md)
- [`qitos.render.events`](qitos/render/events.md)
- [`qitos.render.hooks`](qitos/render/hooks.md)

<a id="group-qitos-trace"></a>
### `qitos.trace`

- [`qitos.trace`](qitos/trace.md)
- [`qitos.trace.events`](qitos/trace/events.md)
- [`qitos.trace.schema`](qitos/trace/schema.md)
- [`qitos.trace.writer`](qitos/trace/writer.md)

## Source Index

- [qitos/](https://github.com/Qitor/qitos/tree/main/qitos)