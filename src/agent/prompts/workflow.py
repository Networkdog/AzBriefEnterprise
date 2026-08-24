"""Workflow architecture description.

Brief overview included in Planning phase only.
"""

WORKFLOW_PROMPT = """## Workflow Architecture
This agent operates in a structured **Plan-Execute-Evaluate** loop:
1. **Planning Phase**: Analyze the Azure Update, search Microsoft Learn docs, create a structured analysis plan.
2. **Execution Phase**: Execute each planned task by calling appropriate tools.
3. **Evaluation Phase**: Assess completeness and quality of gathered information.
4. **Report Phase**: Generate the final analysis report based on all collected data.

The loop may iterate: if evaluation finds gaps, tasks are revised and re-executed.
"""
