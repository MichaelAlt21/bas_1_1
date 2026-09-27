import type { Mission } from '../../shared/types';
import { aircraftIcon } from '../map/aircraftIcons';
import { UI_ICON } from '../map/iconSvg';

export default function AircraftTag({mission}:{mission:Pick<Mission,'aircraft_id'|'profile_id'|'color'>}){
 return <span className="aircraft-tag"><img src={aircraftIcon(mission.profile_id,mission.color,UI_ICON)} alt=""/><strong>{mission.aircraft_id}</strong></span>;
}
