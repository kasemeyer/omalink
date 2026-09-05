/* Omalink theme bridge — rendered by Omarchy on every theme change.
   Source template: omalink/data/omalink.css.tpl (installed to
   ~/.config/omarchy/themed/). Do not edit the rendered copy. */
/* mode: {{ mode }} */

@define-color accent_color {{ accent }};
@define-color accent_bg_color {{ accent }};
@define-color accent_fg_color {{ background }};
@define-color window_bg_color {{ background }};
@define-color window_fg_color {{ foreground }};
@define-color view_bg_color {{ mix background foreground 3 }};
@define-color view_fg_color {{ foreground }};
@define-color headerbar_bg_color {{ mix background foreground 5 }};
@define-color headerbar_fg_color {{ foreground }};
@define-color headerbar_border_color {{ mix background foreground 12 }};
@define-color sidebar_bg_color {{ mix background foreground 6 }};
@define-color sidebar_fg_color {{ foreground }};
@define-color sidebar_backdrop_color {{ mix background foreground 3 }};
@define-color secondary_sidebar_bg_color {{ mix background foreground 4 }};
@define-color secondary_sidebar_fg_color {{ foreground }};
@define-color card_bg_color {{ mix background foreground 8 }};
@define-color card_fg_color {{ foreground }};
@define-color dialog_bg_color {{ mix background foreground 6 }};
@define-color dialog_fg_color {{ foreground }};
@define-color popover_bg_color {{ mix background foreground 8 }};
@define-color popover_fg_color {{ foreground }};
@define-color success_color {{ green }};
@define-color success_bg_color {{ green }};
@define-color success_fg_color {{ background }};
@define-color warning_color {{ yellow }};
@define-color warning_bg_color {{ yellow }};
@define-color warning_fg_color {{ background }};
@define-color error_color {{ red }};
@define-color error_bg_color {{ red }};
@define-color error_fg_color {{ background }};
@define-color destructive_color {{ red }};
@define-color destructive_bg_color {{ red }};
@define-color destructive_fg_color {{ background }};

/* Contact avatars: replace libadwaita's stock 14-color palette with the
   active theme's ANSI colors so the "colored balls" match the theme. */
avatar.color1  { background-image: none; background-color: {{ blue }};           color: {{ background }}; }
avatar.color2  { background-image: none; background-color: {{ cyan }};           color: {{ background }}; }
avatar.color3  { background-image: none; background-color: {{ green }};          color: {{ background }}; }
avatar.color4  { background-image: none; background-color: {{ yellow }};         color: {{ background }}; }
avatar.color5  { background-image: none; background-color: {{ magenta }};        color: {{ background }}; }
avatar.color6  { background-image: none; background-color: {{ red }};            color: {{ background }}; }
avatar.color7  { background-image: none; background-color: {{ bright_blue }};    color: {{ background }}; }
avatar.color8  { background-image: none; background-color: {{ bright_cyan }};    color: {{ background }}; }
avatar.color9  { background-image: none; background-color: {{ bright_green }};   color: {{ background }}; }
avatar.color10 { background-image: none; background-color: {{ bright_yellow }};  color: {{ background }}; }
avatar.color11 { background-image: none; background-color: {{ bright_magenta }}; color: {{ background }}; }
avatar.color12 { background-image: none; background-color: {{ bright_red }};     color: {{ background }}; }
avatar.color13 { background-image: none; background-color: {{ accent }};         color: {{ background }}; }
avatar.color14 { background-image: none; background-color: {{ muted }};          color: {{ background }}; }
