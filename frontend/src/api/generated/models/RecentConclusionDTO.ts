/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { UnavailableBlockDTO } from './UnavailableBlockDTO';
export type RecentConclusionDTO = {
    unavailable_blocks?: Array<UnavailableBlockDTO>;
    task_id: string;
    task_type: RecentConclusionDTO.task_type;
    ticker: (string | null);
    effective_trade_date: (string | null);
    selected_layers: Array<string>;
    completed_at: string;
    conclusion_summary?: (string | null);
    risk_flag: boolean;
    risk_hint: (string | null);
    has_report: boolean;
    updated_at: string;
};
export namespace RecentConclusionDTO {
    export enum task_type {
        SINGLE_STOCK = 'SINGLE_STOCK',
        MARKET_WIDE = 'MARKET_WIDE',
        DAILY_RESEARCH = 'DAILY_RESEARCH',
    }
}

