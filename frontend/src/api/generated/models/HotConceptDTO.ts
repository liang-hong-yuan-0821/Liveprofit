/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BarDTO } from './BarDTO';
import type { DailyChangeDTO } from './DailyChangeDTO';
/**
 * 字段随 sector 体系改名（决策 12 连带）：concept_code/concept_name →
 * sector_code/sector_name；hotness_reason 现场计算版恒 NULL（LLM 未实现）。
 */
export type HotConceptDTO = {
    sector_code: string;
    sector_name: string;
    rank: number;
    hotness_reason: (string | null);
    period_return: (number | null);
    daily_changes: (Array<DailyChangeDTO> | null);
    updated_at: (string | null);
    bars: Array<BarDTO>;
};

