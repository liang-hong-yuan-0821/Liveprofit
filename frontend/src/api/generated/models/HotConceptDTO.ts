/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BarDTO } from './BarDTO';
import type { DailyChangeDTO } from './DailyChangeDTO';
export type HotConceptDTO = {
    concept_code: string;
    concept_name: string;
    rank: number;
    hotness_reason: (string | null);
    period_return: (number | null);
    daily_changes: (Array<DailyChangeDTO> | null);
    updated_at: (string | null);
    bars: Array<BarDTO>;
};

