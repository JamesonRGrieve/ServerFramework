# from sqlalchemy import select
# from sqlalchemy.orm import Session
# from extensions.chains.DB_Chains import ChainLinkRun
# from extensions.chains.DB_Chains import ChainLink, ChainLinkDependency, ExecutionType
# def validate_conditional_dependency(session: Session, dependency):
#     This replaces the check_conditional_dependency_source CHECK constraint.
#     """
#     if dependency.condition_value is not None:
#         # Check if the prerequisite step is a conditional execution type
#         stmt = select(ChainLink).where(
#             ChainLink.id == dependency.prerequisite_chain_link_id,
#             ChainLink.execution_type == ExecutionType.CONDITIONAL,
#         )
#         result = session.execute(stmt).first()
#         if not result:
#             raise ValueError(
#                 f"Conditional dependency (condition_value={dependency.condition_value}) "
#                 f"must point from a conditional step"
#             )
#     return True
# def validate_chain_link_run_parent(session: Session, step_run):
#     """
#     Validate that parent_iteration_id is only set for iteration or parallel iteration steps.
#     if step_run.parent_iteration_id is not None:
#         stmt = select(ChainLink).where(
#             ChainLink.id == step_run.chain_link_id,
#             ChainLink.execution_type.in_(
#                 [ExecutionType.ITERATION, ExecutionType.PARALLEL_ITERATION]
#             ),
#         )
#         result = session.execute(stmt).first()
#         if not result:
#             raise ValueError(
#                 "Parent iteration ID can only be set for iteration or parallel iteration steps"
#             )
#     return True
# def validate_aggregation_step(session: Session, step_run):
#     """
#     Validate that is_aggregation_step is only true for parallel execution steps.
#     This replaces the check_aggregation_step CHECK constraint.
#     """
#             ChainLink.id == step_run.chain_link_id, ChainLink.parallel_execution == True
#         )
#         result = session.execute(stmt).first()
#         if not result:
#             raise ValueError(
#                 "Aggregation step can only be set for parallel execution steps"
#             )
#     return True
# def validate_condition_result(session: Session, step_run):
#     """
#     Validate that condition_result is only set for conditional steps.
#     This replaces the check_condition_result CHECK constraint.
#     """
#     if step_run.condition_result is not None:
#         stmt = select(ChainLink).where(
#         )
#         result = session.execute(stmt).first()
#         if not result:
#             raise ValueError("Condition result can only be set for conditional steps")
#     return True
# # Now you can add these validations to your SessionEvents or in your service layer
# from sqlalchemy import event
# def setup_validators(session_factory):
#     @event.listens_for(ChainLinkDependency, "before_insert")
#     @event.listens_for(ChainLinkDependency, "before_update")
#     def validate_dependency(mapper, connection, target):
#         with session_factory() as session:
#             validate_conditional_dependency(session, target)
#     @event.listens_for(ChainLinkRun, "before_insert")
#         with session_factory() as session:
#             validate_chain_link_run_parent(session, target)
