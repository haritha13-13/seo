# Inputs are supplied for each Run and never baked into a script

A Step may contain placeholders other than `{{username}}`/`{{password}}`. Each distinct name is an Input (text, file or image) the operator fills in on the Executor, whose form is built from the Steps each time. The first Run uses the values live, but the stored script keeps only placeholders and reads the values from the environment at replay (the same mechanism as ADR 0002), so one script serves every future Run. The price is that a script replays a fixed sequence of actions: an Input is exactly one value, so "any number of tags" is not supported (use several Inputs), and every Input is required. We rejected recording the typed values into the script because it would silently ignore new values on replay.

If the agent tries to type a literal equal to an Input value instead of the placeholder, the tool refuses, so a script cannot accidentally become value-specific.
