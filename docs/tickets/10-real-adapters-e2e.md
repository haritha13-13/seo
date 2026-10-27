# 10 Real adapters + end-to-end
Do: `AgentScriptGenerator` (wrap existing runner with placeholder mapping), `SubprocessScriptExecutor`; e2e test (opt-in marker) on the-internet.herokuapp.com: create Portal, Credential, Workflow via services, Run twice (generate then replay), assert both succeeded and 2nd did not call the agent.
