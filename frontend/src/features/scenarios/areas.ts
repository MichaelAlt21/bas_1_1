import type { Polygon } from 'geojson';
import type { Draft } from '../../shared/types';

export const MAX_SURVEY_AREAS=200;
export function surveyAreas(draft:Draft):Polygon[]{
 return [...(draft.survey_area?[draft.survey_area]:[]),...(draft.additional_survey_areas||[])];
}
export function withSurveyAreas(draft:Draft,areas:Polygon[]):Draft{
 return {...draft,survey_area:areas[0]||null,additional_survey_areas:areas.slice(1)};
}
