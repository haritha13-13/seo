# Portal Automation

Lets an operator describe a workflow on a web portal once in plain steps, have an agent turn it into a reusable script, and then replay that script on demand with a chosen credential while keeping a log of every run.

## Language

**Portal**:
A website the operator automates, identified by a name and a start URL, and either active or inactive.
_Avoid_: Site, app, target

**Credential**:
A labeled way to sign in to one Portal: a username and password, a Saved Session, or both.
_Avoid_: Account, login, user

**Saved Session**:
The signed-in state of a browser (cookies and local storage) kept against a Credential, so Runs reuse a sign-in the operator did by hand instead of signing in again.
_Avoid_: Auth file, storage state, cookie jar, token

**Capture**:
Signing in by hand in a real browser window so the app can keep the result as the Credential's Saved Session.
_Avoid_: Record, login recording

**Workflow**:
A named, ordered list of Steps to carry out on one Portal.
_Avoid_: Flow, task, job, scenario

**Starts signed in**:
A Workflow setting meaning its Steps assume the browser is already signed in, so a Run loads the Credential's Saved Session instead of using username and password.
_Avoid_: Auto-login, session mode

**Input**:
A named value the operator supplies each time a Workflow runs, written as a placeholder in its Steps; its kind is text, file or image.
_Avoid_: Parameter, variable, argument, form field

**Step**:
One plain-language instruction within a Workflow, at a fixed position.
_Avoid_: Instruction, action, line item

**Generation**:
The one-time process where the agent carries out a Workflow's Steps in a live browser and records what it did to produce a Script Version.
_Avoid_: Training, learning, recording session

**Script Version**:
A numbered, immutable script produced by a Generation for a Workflow; the newest non-stale one is the Current Script Version.
_Avoid_: Script file, automation, macro

**Stale**:
The state of a Workflow's Script Versions after its Steps change, meaning none of them may be run until a new Generation.
_Avoid_: Outdated, expired, invalid

**Run**:
One attempt to carry out a Workflow with a chosen Credential, including a Generation if no Current Script Version exists.
_Avoid_: Execution, job, task instance

**Executor**:
The screen where the operator picks a Portal, Workflow and Credential, verifies the details and starts a Run.
_Avoid_: Runner screen, launcher

**Run Log**:
The recorded outcome of a Run: status, timing, credential label, Script Version, captured output and failure screenshot.
_Avoid_: Audit trail, history entry

**Verification**:
The operator's explicit confirmation on the Executor that the chosen details are correct before a Run may start.
_Avoid_: Approval, sign-off
