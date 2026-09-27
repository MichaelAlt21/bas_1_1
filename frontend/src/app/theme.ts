/** Colours shared by the map layers and the charts; the page itself uses the CSS tokens in styles.css. */
export const BRAND={orange:'#F26B1D',orangeDark:'#C9530F',charcoal:'#22262B',graphite:'#3A4047',gray:'#EEF0F2'};

// Survey areas carry the brand orange; everything that constrains the flight uses
// other hues so a restriction is never mistaken for work.
export const MAP_COLORS:Record<string,string>={
 survey:BRAND.orange,
 allowed:'#2563EB',
 forbidden:'#D92D20',
 launch_landing:BRAND.charcoal,
 alternate:'#7C3AED',
 task:BRAND.orange,
 package:BRAND.graphite,
};

export const SCHEDULE_COLORS={charging:'#F59E0B',waiting:'#9AA3AD',handling:'#C7CDD3',swap:'#8C959F'};
