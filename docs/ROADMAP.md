# After local acceptance

Keep this order unless a concrete use case changes it. A box in an architecture diagram is not an implementation obligation.

1. **Finish and freeze local acceptance.** Resolve dependencies, keep the npm lock current, run real PostgreSQL/container/browser/restart tests and record versions. Reassess the reference engine using actual user documents, not demo appearance.
2. **Add a real Bedrock provider behind the existing LLM contract.** Start with an explicitly authorized, small paid test. Add structured-output/evidence validation, prompt/model/config provenance, timeouts and spend limits. Keep the deterministic engine as a transparent baseline. Do not claim it reproduces Paper B.
3. **Prepare AWS as a separate milestone.** ECR + ECS/Fargate, RDS PostgreSQL and an S3 adapter. Before any shared endpoint, implement identity and resource authorization, secret handling, HTTPS, quotas and operational recovery. Decide whether model latency now justifies a durable worker/queue. No Kubernetes or loose hand-managed EC2 is needed merely to demonstrate the app.
4. **Add another client only for a demonstrated workflow.** Inbound MCP should call the same authorized Core primitives; outbound connectors operate under Core-controlled access/provenance. iOS should be a native client of the same API. Neither requires moving business logic into the client, but both require real integration work.

Do not begin any of these while local integration is still the active milestone. The next deliverable is a working local app, not a cloud bill or an enlarged architecture diagram.
