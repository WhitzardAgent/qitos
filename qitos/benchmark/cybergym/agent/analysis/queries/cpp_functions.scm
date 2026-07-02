(function_definition declarator: (_) @function.declarator body: (compound_statement) @function.body) @function.definition
(call_expression function: (_) @call.function arguments: (argument_list) @call.arguments) @call.expression
[(namespace_definition) (class_specifier) (struct_specifier)] @scope
