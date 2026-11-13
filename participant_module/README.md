# Stage 1 assessment module

Entry point: `participant_module.adapter:AssessmentModule`.

This CPU-only baseline extracts page text with the kit's `pypdf`, matches conservative criterion-specific evidence patterns, and returns page references with short quotations. It does not use report-stated TRL labels as evidence. It marks unsupported criteria as `insufficient evidence` and completes a TRL only when the checklist milestones form a contiguous chain and criteria C10-C12 are also supported. This conservative completion rule can abstain on genuine higher-readiness cases when reports omit intermediate or cross-cutting details.

Connect it with the `module_root` set to the directory containing `participant_module`, `package_root` set to `participant_module`, and entrypoint above. No external models, downloads, or extra packages are needed beyond the base environment.
