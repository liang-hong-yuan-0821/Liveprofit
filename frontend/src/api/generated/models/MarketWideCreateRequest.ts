/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { AnalysisOptions } from './AnalysisOptions';
export type MarketWideCreateRequest = {
    task_type: string;
    ticker?: null;
    requested_trade_date: string;
    selected_layers: Array<'market' | 'sector' | 'stock' | 'screening' | 'position'>;
    analysis_options?: (AnalysisOptions | null);
};

