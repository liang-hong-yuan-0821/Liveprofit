/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ActiveTaskDTO } from './ActiveTaskDTO';
import type { PendingActionDTO } from './PendingActionDTO';
import type { RecentConclusionDTO } from './RecentConclusionDTO';
export type AnalysisDashboardDTO = {
    pending_actions: Array<PendingActionDTO>;
    active_tasks: Array<ActiveTaskDTO>;
    recent_conclusions: Array<RecentConclusionDTO>;
    generated_at: string;
};

