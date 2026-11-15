//! The viewer's languages (English, French) and its message catalogue.
//!
//! Every text the window shows is a [`Msg`]: one entry per message id with
//! its English and its French words. A text with values in it names them
//! `{name}` and is filled by [`Text::render`]. The worker's messages travel in
//! English with a code (see `gui_worker.py`); [`worker_msg`] maps a code to
//! its entry, and [`render_worker`] renders it in the chosen language, the
//! worker's own English words when the code is unknown.
//!
//! French typography: « » around a quoted word, a non-breaking space
//! (U+00A0, written `\u{a0}`) before `:` `;` `?` `!` and inside « », a
//! decimal comma and non-breaking spaces between thousands in the numbers
//! the viewer formats ([`num`], [`count`], [`french_number`]). The default
//! fonts have no narrow non-breaking space (U+202F): U+00A0 is used.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use crate::plots::fmt_g;

/// A language of the window.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Lang {
    En,
    Fr,
}

/// The non-breaking space of French typography.
pub const NBSP: char = '\u{a0}';

impl Lang {
    pub const ALL: [Lang; 2] = [Lang::En, Lang::Fr];

    /// `en` or `fr`, as `--lang` and the settings file write it.
    pub fn code(self) -> &'static str {
        match self {
            Lang::En => "en",
            Lang::Fr => "fr",
        }
    }

    /// The language of `--lang`: `en` or `fr` (any case).
    pub fn parse(text: &str) -> Option<Lang> {
        Lang::ALL
            .into_iter()
            .find(|l| l.code().eq_ignore_ascii_case(text.trim()))
    }

    /// The name of the language in itself, as the selector lists it.
    pub fn native_name(self) -> &'static str {
        match self {
            Lang::En => "English",
            Lang::Fr => "Français",
        }
    }

    /// The language of a locale (`fr-FR`, `fr_CA.UTF-8`, `en-US`...):
    /// French when it starts with `fr`, English otherwise.
    pub fn from_locale(locale: Option<&str>) -> Lang {
        match locale {
            Some(l) if l.get(..2).is_some_and(|p| p.eq_ignore_ascii_case("fr")) => Lang::Fr,
            _ => Lang::En,
        }
    }

    /// The language of the operating system's locale.
    pub fn system() -> Lang {
        Lang::from_locale(sys_locale::get_locale().as_deref())
    }

    /// The words of `msg` in this language.
    pub fn t(self, msg: Msg) -> &'static str {
        msg.text(self)
    }

    /// `msg` with its `{name}` placeholders filled.
    pub fn f(self, msg: Msg, args: &[(&str, &str)]) -> String {
        fill(msg.text(self), |name| {
            args.iter()
                .find(|(n, _)| *n == name)
                .map(|(_, v)| v.to_string())
        })
        .unwrap_or_else(|| msg.text(self).to_string())
    }
}

/// Defines [`Msg`]: `Id = "English", "French";` per entry.
macro_rules! catalogue {
    ($( $(#[$doc:meta])* $id:ident = $en:expr, $fr:expr; )*) => {
        /// A message of the window, by id.
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
        pub enum Msg {
            $( $(#[$doc])* $id, )*
        }

        impl Msg {
            /// Every message, in the catalogue's order.
            #[cfg(test)]
            pub const ALL: &'static [Msg] = &[$(Msg::$id,)*];

            /// The words of the message in `lang`.
            pub fn text(self, lang: Lang) -> &'static str {
                match self {
                    $( Msg::$id => match lang {
                        Lang::En => $en,
                        Lang::Fr => $fr,
                    }, )*
                }
            }

            /// The message id, as written in the catalogue.
            #[cfg(test)]
            pub fn id(self) -> &'static str {
                match self {
                    $( Msg::$id => stringify!($id), )*
                }
            }
        }
    };
}

catalogue! {
    // --- Language -------------------------------------------------------------
    Language = "Language", "Langue";
    LanguageSystem = "System ({language})", "Système ({language})";
    LanguageNote = "--lang on the command line picks the language of one run without saving it.",
        "--lang sur la ligne de commande choisit la langue d'une exécution sans l'enregistrer.";

    // --- Top bar --------------------------------------------------------------
    Open = "Open…", "Ouvrir…";
    OpenTip = "Choose a recording", "Choisir un enregistrement";
    Recent = "Recent", "Récents";
    PathHint = "Path of a .dat / .h5 / .hdf5 / .tdms / .sgy file (or drop it on the window)",
        "Chemin d'un fichier .dat / .h5 / .hdf5 / .tdms / .sgy (ou déposez-le sur la fenêtre)";
    Load = "Load", "Charger";
    Log = "Log", "Journal";
    LogTip = "What the Python worker printed", "Ce que le processus Python a écrit";
    Settings = "Settings", "Réglages";
    SettingsTip = "Python interpreter, language, auto-update",
        "Interpréteur Python, langue, mise à jour automatique";

    // --- Status bar -----------------------------------------------------------
    Starting = "Starting `{command}`…", "Démarrage de `{command}`…";
    NotRunning = "The Python worker is not running.", "Le processus Python ne tourne pas.";
    Restart = "Restart", "Redémarrer";
    Stopped = "Worker stopped.", "Processus Python arrêté.";
    Opening = "Opening {path}…", "Ouverture de {path}…";
    ProgressPulses = "{stage} {done} / {total} pulses", "{stage} {done} / {total} tirs";
    Computing = "computing…", "calcul…";
    Stopping = "Stopping…", "Arrêt…";
    Cancel = "Cancel", "Annuler";
    Settling = "Waiting for the options to settle…", "En attente de la fin des modifications…";
    StageReading = "reading", "lecture";

    // --- Views ----------------------------------------------------------------
    ViewInfo = "Info", "Infos";
    ViewHeatmap = "Waterfall", "Cascade";
    ViewBands = "Band heatmaps", "Cartes de bandes";
    ViewFft = "Spectrum (FFT)", "Spectre (FFT)";
    ViewTrace = "Time trace", "Trace temporelle";
    ViewScope = "Oscilloscope", "Oscilloscope";
    ViewInspect = "Inspect location", "Inspecter une position";
    DescInfo = "Header metadata and statistics of the default channel.",
        "Métadonnées de l'en-tête et statistiques du canal par défaut.";
    DescHeatmap = "Distance x time waterfall, decimated to screen resolution.",
        "Cascade distance × temps, décimée à la résolution de l'écran.";
    DescBands = "One waterfall per temporal frequency band, plus the global one.",
        "Une cascade par bande de fréquence temporelle, plus la cascade globale.";
    DescFft = "Temporal spectrum (FFT along the pulses) at one or more positions.",
        "Spectre temporel (FFT le long des tirs) en une ou plusieurs positions.";
    DescTrace = "Time series at one position, min/max envelope.",
        "Série temporelle en une position, enveloppe min/max.";
    DescScope = "Replays the lines (pulses) in real time, looping.",
        "Rejoue les lignes (tirs) en temps réel, en boucle.";
    DescInspect = "One location: waveform and its spectrum.",
        "Une position\u{a0}: forme d'onde et son spectre.";

    // --- Options panel --------------------------------------------------------
    ViewHeading = "View", "Vue";
    OpenToChoose = "Open a recording to choose its options.",
        "Ouvrez un enregistrement pour choisir ses options.";
    Render = "Render", "Calculer";
    RenderChanged = "Render (changed)", "Calculer (modifié)";
    CliDefaults = "CLI defaults", "Défauts de la CLI";
    CliDefaultsTip = "Every option of this view back to the CLI's default",
        "Remet chaque option de cette vue à sa valeur par défaut dans la CLI";
    ScopeControlsNote = "Speed, frame rate and the shown line are set above the plot.",
        "La vitesse, la cadence d'images et la ligne affichée se règlent au-dessus du graphique.";
    Channel = "Channel", "Canal";
    ChannelDefault = "{channel} (default)", "{channel} (défaut)";
    ChannelInfoNote = "The CLI's info always describes the default channel.",
        "L'info de la CLI décrit toujours le canal par défaut.";
    Unit = "Unit", "Unité";
    UnitAsStored = "As stored", "Telle que stockée";
    UnitFlagTip = "The CLI's {flag}", "L'option {flag} de la CLI";
    UnitStoredTip = "The samples as the file stores them",
        "Les échantillons tels que le fichier les stocke";
    OptionTip = "{help}\n\nCLI: {flag}", "{help}\n\nCLI\u{a0}: {flag}";
    AutoTip = "Unchecked: the CLI's default (auto)", "Décoché\u{a0}: le défaut de la CLI (auto)";
    Auto = "auto", "auto";
    Off = "off", "désactivé";
    AutoCliDefault = "auto (CLI default)", "auto (défaut de la CLI)";
    DefaultHint = "default", "défaut";
    Where = "Where", "Où";
    WholeFibre = "Whole fibre (average)", "Toute la fibre (moyenne)";
    PositionsMetres = "Positions (m)", "Positions (m)";
    PositionsTip = "'12.5' or '10,20,30': one curve per position",
        "«\u{a0}12.5\u{a0}» ou «\u{a0}10,20,30\u{a0}»\u{a0}: une courbe par position (avec un point décimal, comme la CLI)";
    RangeMetres = "Range (m)", "Plage (m)";
    RangeTip = "'50:100': averaged", "«\u{a0}50:100\u{a0}»\u{a0}: moyennée";
    MiddleOfFibre = "Middle of the fibre", "Milieu de la fibre";
    IndexChoice = "Index", "Index";
    PositionMetres = "Position (m)", "Position (m)";

    // Option groups.
    GroupWindow = "Time and distance window", "Fenêtre de temps et de distance";
    GroupDecimation = "Decimation", "Décimation";
    GroupColour = "Colour", "Couleur";
    GroupBands = "Bands (Hz)", "Bandes (Hz)";
    GroupResolution = "Resolution", "Résolution";
    GroupPositions = "Positions", "Positions";
    GroupSpectrum = "Spectrum", "Spectre";
    GroupTimeWindow = "Time window", "Fenêtre de temps";
    GroupPosition = "Position", "Position";
    GroupDisplay = "Display", "Affichage";
    GroupLoop = "Loop", "Boucle";
    GroupLocation = "Location", "Emplacement";
    GroupWaveform = "Waveform and spectrum", "Forme d'onde et spectre";
    GroupOther = "Other options", "Autres options";

    // Option names: the English is the CLI's dest made readable (`humanize`).
    OptChannel = "Channel", "Canal";
    OptStartTime = "Start time", "Début";
    OptDuration = "Duration", "Durée";
    OptStartDistance = "Start distance", "Distance de début";
    OptEndDistance = "End distance", "Distance de fin";
    OptMaxPulses = "Max pulses", "Tirs max";
    OptMaxTimeBins = "Max time bins", "Cellules de temps max";
    OptMaxSpaceBins = "Max space bins", "Cellules de distance max";
    OptReduce = "Reduce", "Réduction";
    OptVariance = "Variance", "Variance";
    OptDb = "Db", "dB";
    OptVmin = "Vmin", "Vmin";
    OptVmax = "Vmax", "Vmax";
    OptCmap = "Cmap", "Palette";
    OptF0 = "F0", "F0";
    OptF1 = "F1", "F1";
    OptF2 = "F2", "F2";
    OptF3 = "F3", "F3";
    OptNoDetrend = "No detrend", "Sans retrait de tendance";
    OptPosition = "Position", "Position";
    OptPositionRange = "Position range", "Plage de positions";
    OptWindow = "Window", "Fenêtre";
    OptFmax = "Fmax", "Fmax";
    OptSubsampleTime = "Subsample time", "Sous-échantillonnage";
    OptMaxPoints = "Max points", "Points max";
    OptIndex = "Index", "Index";
    OptClipPercentile = "Clip percentile", "Centile d'écrêtage";
    OptFftLog = "Fft log", "FFT log";
    OptSpeed = "Speed", "Vitesse";
    OptFps = "Fps", "Images/s";
    OptLine = "Line", "Ligne";

    // The CLI's help of each option: the English is the CLI's text, word for
    // word; a help the CLI words differently is shown as the CLI wrote it.
    HelpChannel = "Channel (default depends on mode).", "Canal (le défaut dépend du mode).";
    HelpChannelScript = "Channel (default depends on mode). Ignored with --script.",
        "Canal (le défaut dépend du mode). Ignoré avec --script.";
    HelpStartTime = "Skip the first seconds.", "Ignore les premières secondes.";
    HelpLoopStart = "Loop start (s).", "Début de la boucle (s).";
    HelpDuration = "Duration to process (s).", "Durée à traiter (s).";
    HelpLoopDuration = "Loop duration (s). Default: whole file.",
        "Durée de la boucle (s). Défaut\u{a0}: tout le fichier.";
    HelpDurationRead = "Duration to read (s).", "Durée à lire (s).";
    HelpStartDistance = "Lower spatial bound (m).", "Borne spatiale inférieure (m).";
    HelpEndDistance = "Upper spatial bound (m).", "Borne spatiale supérieure (m).";
    HelpTimeBinsX = "Max time bins (X resolution). Default 2000.",
        "Nombre maximal de cellules de temps (résolution en X). Défaut 2000.";
    HelpTimeBinsY = "Max time bins (Y resolution). Default 2000.",
        "Nombre maximal de cellules de temps (résolution en Y). Défaut 2000.";
    HelpSpaceBinsY = "Max spatial bins (Y resolution). Default 2000.",
        "Nombre maximal de cellules de distance (résolution en Y). Défaut 2000.";
    HelpSpaceBinsX = "Max spatial bins (X resolution). Default 1000 (the full time series is held in RAM).",
        "Nombre maximal de cellules de distance (résolution en X). Défaut 1000 (toute la série temporelle est gardée en mémoire).";
    HelpReduce = "Temporal bin reducer (default mean).",
        "Réduction de chaque cellule de temps (défaut mean).";
    HelpMaxPulses = "Limit the number of pulses read (default: whole file).",
        "Limite le nombre de tirs lus (défaut\u{a0}: tout le fichier).";
    HelpMaxPulsesShort = "Limit the number of pulses read.", "Limite le nombre de tirs lus.";
    HelpDb = "dB scale (ref = observed max).", "Échelle en dB (référence = maximum observé).";
    HelpDbFft = "Y axis in dB (ref = max).", "Axe Y en dB (référence = maximum).";
    HelpVmin = "Lower colorbar bound (auto).", "Borne inférieure de la barre de couleurs (auto).";
    HelpVmax = "Upper colorbar bound (auto).", "Borne supérieure de la barre de couleurs (auto).";
    HelpCmap = "matplotlib colormap (default auto).", "Palette matplotlib (défaut auto).";
    HelpVariance = "Color each time bin by its temporal VARIANCE instead of the mean ('log' = log10 of the variance, floored 120 dB below its maximum: a display scale, not the console's log10(1 + variance) of archive exports). Off by default; overrides --reduce and --db.",
        "Colore chaque cellule de temps par sa VARIANCE temporelle au lieu de la moyenne («\u{a0}log\u{a0}» = log10 de la variance, avec un plancher à 120 dB sous son maximum\u{a0}: une échelle d'affichage, pas le log10(1 + variance) des exports d'archives de la console). Désactivé par défaut\u{a0}; remplace --reduce et --db.";
    HelpF0 = "Lower edge of the FIRST band (Hz). Default 0 (keeps DC).",
        "Bord inférieur de la PREMIÈRE bande (Hz). Défaut 0 (garde la composante continue).";
    HelpF1 = "Upper edge of band 1 (Hz). REQUIRED.", "Bord supérieur de la bande 1 (Hz). OBLIGATOIRE.";
    HelpF2 = "Upper edge of band 2 (Hz). Optional.", "Bord supérieur de la bande 2 (Hz). Facultatif.";
    HelpF3 = "Upper edge of band 3 (Hz). Optional.", "Bord supérieur de la bande 3 (Hz). Facultatif.";
    HelpNoDetrendBands = "Keep the raw signal (incl. DC) before the band FFT. By default each position is detrended (mean + linear trend removed) to avoid edge-ringing artifacts.",
        "Garde le signal brut (composante continue comprise) avant la FFT des bandes. Par défaut, la tendance de chaque position est retirée (moyenne + tendance linéaire) pour éviter les oscillations parasites aux bords.";
    HelpNoDetrendFft = "Do not remove the per-position mean before the FFT.",
        "Ne retire pas la moyenne de chaque position avant la FFT.";
    HelpNoDetrendInspect = "Only remove DC, keep the linear trend.",
        "Retire seulement la composante continue, garde la tendance linéaire.";
    HelpPositionFft = "Position(s) in m: '12.5' or '10,20,30'.",
        "Position(s) en m\u{a0}: «\u{a0}12.5\u{a0}» ou «\u{a0}10,20,30\u{a0}».";
    HelpPositionTrace = "Position (m).", "Position (m).";
    HelpPositionInspect = "Position in meters (converted to the nearest index).",
        "Position en mètres (convertie en l'index le plus proche).";
    HelpPositionRange = "Range of positions to average (m): '50:100'.",
        "Plage de positions à moyenner (m)\u{a0}: «\u{a0}50:100\u{a0}».";
    HelpWindow = "Time window (default hann).", "Fenêtre temporelle (défaut hann).";
    HelpWindowInspect = "FFT time window (default hann).",
        "Fenêtre temporelle de la FFT (défaut hann).";
    HelpFmax = "Limit X axis (Hz). Default Nyquist.", "Limite l'axe X (Hz). Défaut\u{a0}: Nyquist.";
    HelpFmaxInspect = "Limit the FFT frequency axis (Hz). Default Nyquist.",
        "Limite l'axe des fréquences de la FFT (Hz). Défaut\u{a0}: Nyquist.";
    HelpSubsampleFft = "One pulse out of N.", "Un tir sur N.";
    HelpSubsample = "Keep only one pulse out of N (default 1).",
        "Ne garde qu'un tir sur N (défaut 1).";
    HelpMaxPointsTrace = "Max points in the line plot (min/max envelope). Default 4000.",
        "Nombre maximal de points de la courbe (enveloppe min/max). Défaut 4000.";
    HelpMaxPointsScope = "Max points drawn per line (peak decimation). Default 4000.",
        "Nombre maximal de points tracés par ligne (décimation par pics). Défaut 4000.";
    HelpIndex = "Location/column index (default: middle of the fiber).",
        "Index de l'emplacement (colonne) (défaut\u{a0}: milieu de la fibre).";
    HelpClipPercentile = "Waveform y-axis percentile clip (default 99; 0 disables).",
        "Écrêtage de l'axe Y de la forme d'onde à ce centile (défaut 99\u{a0}; 0 le désactive).";
    HelpFftLog = "Logarithmic FFT magnitude axis.", "Axe d'amplitude de la FFT logarithmique.";

    // --- Central view ---------------------------------------------------------
    WorkerCouldNotRun = "The Python worker could not run", "Le processus Python n'a pas pu démarrer";
    SettingsEllipsis = "Settings…", "Réglages…";
    RestartWorker = "Restart the worker", "Redémarrer le processus Python";
    OpenPrompt = "Open a recording (Open…, a path above, or drop a file on the window).",
        "Ouvrez un enregistrement (Ouvrir…, un chemin ci-dessus, ou déposez un fichier sur la fenêtre).";
    ErrRefused = "The CLI refuses these options", "La CLI refuse ces options";
    ErrInvalidOption = "Invalid option", "Option invalide";
    ErrCancelled = "Cancelled", "Annulé";
    ErrFile = "File error", "Erreur de fichier";
    ErrOther = "Error", "Erreur";
    RenderAgain = "Render again", "Recalculer";
    WarningCount = "{count} warning(s)", "{count} avertissement(s)";
    PressRender = "{view} of {name}: press Render.", "{view} de {name}\u{a0}: appuyez sur Calculer.";
    OptionsChanged = "The options changed: press Render to update the view.",
        "Les options ont changé\u{a0}: appuyez sur Calculer pour mettre la vue à jour.";

    // Info view.
    InfoMode = "Mode", "Mode";
    InfoContentType = "Content type", "Type de contenu";
    InfoQuantity = "Quantity", "Grandeur";
    InfoGauge = "Gauge length", "Longueur de jauge";
    NotRecorded = "not recorded", "non enregistrée";
    InfoTrigger = "Trigger frequency", "Fréquence de tir";
    InfoLineRate = "Line rate used", "Cadence de lignes utilisée";
    InfoTimeCorrection = "Time correction", "Correction du temps";
    NoneRecorded = "none recorded", "aucune enregistrée";
    InfoStart = "Start", "Début";
    InfoPulses = "Pulses", "Tirs";
    InfoDuration = "Duration", "Durée";
    InfoPositions = "Positions", "Positions";
    InfoPositionsValue = "{count} x {step} m = {length} m", "{count} × {step} m = {length} m";
    InfoCompression = "Compression", "Compression";
    InfoChannels = "Channels", "Canaux";
    InfoChannelsValue = "{channels} (default {default})", "{channels} (défaut {default})";
    InfoCliText = "audace-display info", "info d'audace-display";

    // Zoomable plots.
    ZoomWindow = "Zoom: {t0}–{t1} s, {d0}–{d1} m", "Zoom\u{a0}: {t0}–{t1} s, {d0}–{d1} m";
    ZoomTime = "Zoom: {t0}–{t1} s", "Zoom\u{a0}: {t0}–{t1} s";
    ResetZoom = "Reset zoom", "Réinitialiser le zoom";
    ZoomHint = "Ctrl+scroll to zoom, right-drag a box, drag or scroll to pan, double-click to reset: the window is recomputed.",
        "Ctrl+molette pour zoomer, glisser avec le bouton droit pour un cadre, glisser ou défiler pour se déplacer, double-clic pour revenir\u{a0}: la fenêtre est recalculée.";
    Bins = "{rows} x {cols} bins", "{rows} × {cols} cellules";
    Points = "{count} points", "{count} points";
    AxisDistance = "Distance (m)", "Distance (m)";
    AxisTime = "Time (s)", "Temps (s)";
    AxisFrequencyNyquist = "Frequency (Hz)   --   Nyquist = {nyquist} Hz",
        "Fréquence (Hz)   —   Nyquist = {nyquist} Hz";
    AxisFrequency = "Frequency (Hz)", "Fréquence (Hz)";
    AxisTimeFromStart = "Time from selected start (s)", "Temps depuis le début choisi (s)";
    FftUnavailable = "FFT unavailable: signal too short", "FFT indisponible\u{a0}: signal trop court";

    // Oscilloscope.
    ScopeEnded = "The oscilloscope session ended (the worker restarted): press Render.",
        "La session d'oscilloscope s'est terminée (le processus Python a redémarré)\u{a0}: appuyez sur Calculer.";
    Pause = "⏸ Pause", "⏸ Pause";
    Play = "▶ Play", "▶ Lecture";
    Speed = "Speed", "Vitesse";
    Fps = "fps", "img/s";
    LineInLoop = "line in the loop", "ligne dans la boucle";
    PauseToChoose = "Pause to choose a line", "Mettez en pause pour choisir une ligne";
    ScopeReadout = "line {line} / {total}   t = {t} s   ({rate} lines/s x {speed})",
        "ligne {line} / {total}   t = {t} s   ({rate} lignes/s × {speed})";

    // --- Settings and log windows ---------------------------------------------
    PythonInterpreter = "Python interpreter", "Interpréteur Python";
    PythonInterpreterHelp = "The command that runs the Python where audace-display and invisensing are installed: `python3`, `py -3`, or the full path of a virtual environment's interpreter (quote a path with spaces).",
        "La commande qui lance le Python où audace-display et invisensing sont installés\u{a0}: `python3`, `py -3`, ou le chemin complet de l'interpréteur d'un environnement virtuel (entre guillemets si le chemin contient des espaces).";
    ApplyRestart = "Apply and restart", "Appliquer et redémarrer";
    DefaultButton = "Default", "Par défaut";
    Runs = "Runs: {command}", "Exécute\u{a0}: {command}";
    PythonOverride = "--python on the command line overrides the saved interpreter for this run.",
        "--python sur la ligne de commande remplace l'interpréteur enregistré pour cette exécution.";
    InstallHeading = "Install in that Python", "Installer dans ce Python";
    InstallComment = "# or, from a checkout of the audace-display repository:",
        "# ou, depuis un clone du dépôt audace-display\u{a0}:";
    CheckoutPlaceholder = "<path to audace-display>", "<chemin d'audace-display>";
    AutoRender = "Update the view automatically when an option changes",
        "Mettre la vue à jour automatiquement quand une option change";
    AutoRenderTip = "Off: press Render after changing options (large files).",
        "Désactivé\u{a0}: appuyez sur Calculer après avoir changé les options (gros fichiers).";
    SavedIn = "Saved in {path}", "Enregistré dans {path}";
    WorkerLog = "Worker log", "Journal du processus Python";
    LogEmpty = "(nothing written to stderr)", "(rien n'a été écrit sur stderr)";

    // --- Notices and the worker's life cycle ----------------------------------
    SettingsUnreadable = "Settings: {path}: {error}; defaults used",
        "Réglages\u{a0}: {path}\u{a0}: {error}\u{a0}; valeurs par défaut utilisées";
    SettingsNotSaved = "The settings were not saved: {error}",
        "Les réglages n'ont pas été enregistrés\u{a0}: {error}";
    NoConfigDir = "no configuration directory", "pas de dossier de configuration";
    PathError = "{path}: {error}", "{path}\u{a0}: {error}";
    HelloTimeout = "`{command}` did not answer within {seconds} s.",
        "`{command}` n'a pas répondu en {seconds} s.";
    CancelStuck = "A cancelled computation did not stop: the worker was restarted.",
        "Un calcul annulé ne s'est pas arrêté\u{a0}: le processus Python a été redémarré.";
    WorkerSays = "Worker: {message}", "Processus Python\u{a0}: {message}";
    ProtocolMismatch = "audace-display {version} speaks protocol {worker}, this viewer protocol {viewer}: install the audace-display that matches the viewer.",
        "audace-display {version} parle le protocole {worker}, ce visualiseur le protocole {viewer}\u{a0}: installez l'audace-display qui correspond au visualiseur.";
    CannotOpen = "Cannot open the file: {reason}", "Impossible d'ouvrir le fichier\u{a0}: {reason}";
    SetNoOption = "--set: the {view} view has no option '{option}'",
        "--set\u{a0}: la vue {view} n'a pas d'option «\u{a0}{option}\u{a0}»";
    SetNotNumber = "--set {option}: '{value}' is not a number",
        "--set {option}\u{a0}: «\u{a0}{value}\u{a0}» n'est pas un nombre";
    UnitNotHere = "--unit: no unit '{unit}' here", "--unit\u{a0}: pas d'unité «\u{a0}{unit}\u{a0}» ici";
    DialogTitle = "Open a recording", "Ouvrir un enregistrement";
    DialogRecordings = "Audace recordings", "Enregistrements Audace";
    DialogAllFiles = "All files", "Tous les fichiers";
    DialogFailed = "The file dialog could not be shown; type the path instead.",
        "La boîte de dialogue n'a pas pu s'afficher\u{a0}; tapez le chemin à la place.";
    InstallHint = "The viewer needs audace-display (with its GUI worker) and invisensing installed in the Python it runs (`{interpreter}`):\n    {interpreter} -m pip install audace-display\nor, from a checkout of audace-display:\n    {interpreter} -m pip install -e <path to audace-display>\nThe interpreter can be changed in Settings.",
        "Le visualiseur a besoin d'audace-display (avec son processus pour l'interface) et d'invisensing, installés dans le Python qu'il lance (`{interpreter}`)\u{a0}:\n    {interpreter} -m pip install audace-display\nou, depuis un clone d'audace-display\u{a0}:\n    {interpreter} -m pip install -e <chemin d'audace-display>\nL'interpréteur se change dans Réglages.";
    WithDetail = "{message}:\n{detail}", "{message}\u{a0}:\n{detail}";
    Paragraphs = "{first}\n\n{second}", "{first}\n\n{second}";
    CannotStart = "cannot start `{command}`: {error}", "impossible de lancer `{command}`\u{a0}: {error}";
    UnbalancedQuote = "unbalanced {quote} in the interpreter command",
        "{quote} non refermé dans la commande de l'interpréteur";
    EmptyCommand = "the interpreter command is empty", "la commande de l'interpréteur est vide";
    InputClosed = "the worker's input is closed", "l'entrée du processus Python est fermée";
    WriteFailed = "writing to the worker: {error}", "écriture vers le processus Python\u{a0}: {error}";
    WorkerExited = "the worker exited", "le processus Python s'est arrêté";
    ReadFailed = "reading the worker: {error}", "lecture du processus Python\u{a0}: {error}";
    OutputStoppedLine = "the worker's output stopped inside a header line",
        "la sortie du processus Python s'est arrêtée au milieu d'une ligne d'en-tête";
    OutputStoppedArray = "the worker's output stopped inside the array '{name}'",
        "la sortie du processus Python s'est arrêtée au milieu du tableau «\u{a0}{name}\u{a0}»";
    BadMessage = "unreadable message from the worker: {detail}",
        "message illisible du processus Python\u{a0}: {detail}";
    BadArray = "bad array from the worker: {detail}", "tableau invalide du processus Python\u{a0}: {detail}";

    // --- The worker's messages, by code (`gui_worker.py`) ---------------------
    // The English is the worker's or the CLI's wording; in English the viewer
    // shows the worker's own text, so these English texts are for reference.
    WCancelled = "cancelled.", "Annulé.";
    WFileNotFound = "file not found: {path}", "fichier introuvable\u{a0}: {path}";
    WInternal = "unexpected {exception}: {detail}", "erreur inattendue ({exception})\u{a0}: {detail}";
    WProtocolMismatch = "the viewer speaks protocol {client}, this audace-display {version} speaks protocol {protocol}: install the audace-display that matches the viewer.",
        "le visualiseur parle le protocole {client}, cet audace-display {version} parle le protocole {protocol}\u{a0}: installez l'audace-display qui correspond au visualiseur.";
    WScopeSessionGone = "no such oscilloscope session (render the scope again).",
        "cette session d'oscilloscope n'existe plus (relancez le calcul de l'oscilloscope).";
    WUnknownCommand = "unknown command {command}.", "commande inconnue {command}.";
    WRequestField = "the request needs '{name}' ({type}).",
        "la requête doit contenir «\u{a0}{name}\u{a0}» ({type}).";
    WViewUnknown = "unknown view '{view}'. Known: {known}.",
        "vue inconnue «\u{a0}{view}\u{a0}». Connues\u{a0}: {known}.";
    WOptionUnknown = "the {view} view has no option '{option}'.",
        "la vue {view} n'a pas d'option «\u{a0}{option}\u{a0}».";
    WOptionTakesValue = "{flag} takes a value, not true/false.", "{flag} prend une valeur, pas vrai/faux.";
    WOptionUnsupportedValue = "{flag}: unsupported value {value}.",
        "{flag}\u{a0}: valeur non prise en charge {value}.";
    WOptionNotBool = "{flag} is true or false, got {value}.", "{flag} vaut vrai ou faux, reçu {value}.";
    WUnitNotOffered = "the installed CLI cannot show '{unit}' for the {view} view.",
        "la CLI installée ne sait pas afficher «\u{a0}{unit}\u{a0}» pour la vue {view}.";
    WOptionsNotObject = "'options' must be an object.", "«\u{a0}options\u{a0}» doit être un objet.";
    WScopeUnavailable = "the oscilloscope needs invisensing >= 1.1.0.",
        "l'oscilloscope nécessite invisensing >= 1.1.0.";
    WBandsNeedF1 = "bandheatmaps needs at least --f1 (upper edge of band 1, in Hz).",
        "bandheatmaps nécessite au moins --f1 (bord supérieur de la bande 1, en Hz).";
    WBandsF3NeedsF2 = "--f3 requires --f2 (bands must be contiguous).",
        "--f3 nécessite --f2 (les bandes doivent être contiguës).";
    WBandsF0Negative = "--f0 must be >= 0.", "--f0 doit être >= 0.";
    WBandsNotIncreasing = "band edges must be strictly increasing, got [{edges}] Hz.",
        "les bords des bandes doivent être strictement croissants, reçu [{edges}] Hz.";
    WBandsNoneBelowNyquist = "no valid frequency band after clamping to Nyquist.",
        "aucune bande de fréquence valide après limitation à Nyquist.";
    WBandsTooFewPulses = "not enough pulses for a band analysis.",
        "pas assez de tirs pour une analyse par bandes.";
    WBandsTooMuchMemory = "band analysis needs the whole time series in RAM (~{gb} GB: {pulses} pulses x {bins} bins). Restrict with --duration / --max-pulses, or lower --max-space-bins.",
        "l'analyse par bandes a besoin de toute la série temporelle en mémoire (~{gb} GB\u{a0}: {pulses} tirs × {bins} cellules). Restreignez avec --duration / --max-pulses, ou baissez --max-space-bins.";
    WPositionOutside = "position {position} m outside the fiber (range: 0 to {max} m).",
        "position {position} m hors de la fibre (plage\u{a0}: 0 à {max} m).";
    WTraceNoData = "no data read for the trace.", "aucune donnée lue pour la trace.";
    WFftTooFewPulses = "not enough pulses for an FFT.", "pas assez de tirs pour une FFT.";
    WFftTooFewSamples = "not enough time samples for an FFT.",
        "pas assez d'échantillons temporels pour une FFT.";
    WScopeNeedsInvisensing = "oscilloscope mode requires invisensing >= 1.1.0 (O(1) per-line seek).",
        "le mode oscilloscope nécessite invisensing >= 1.1.0 (accès direct à chaque ligne).";
    WIndexOutside = "location index {index} outside the fiber (0 to {last}, i.e. 0 to {max} m).",
        "index d'emplacement {index} hors de la fibre (0 à {last}, soit 0 à {max} m).";
    WInspectTooFewPulses = "not enough pulses to inspect this location.",
        "pas assez de tirs pour inspecter cet emplacement.";
    WUnitNeedsInvisensing = "{option} needs invisensing >= 1.6.0 (pip install -U invisensing).",
        "{option} nécessite invisensing >= 1.6.0 (pip install -U invisensing).";
    WUnitNotAPhase = "{option} shows a phase as {what} ({unit}), and {file} holds '{content}' (content type {content_type}, mode {mode}), not a phase. Drop {option}.",
        "{option} affiche une phase en {what} ({unit}), et {file} contient «\u{a0}{content}\u{a0}» (type de contenu {content_type}, mode {mode}), pas une phase. Retirez {option}.";
    WUnitNoGaugeLength = "{option} needs the gauge length the phase was measured over, and {file} ('{content}', content type {content_type}) does not record its gauge length. Audace writes the gauge length into a phase file only from the release that adds it, so {option} needs a recording made with that release or a later one; a MOZART or MOZART_PLUS phase and a file converted without it carry none. No gauge length is assumed: drop {option} to show the phase in radians.",
        "{option} a besoin de la longueur de jauge sur laquelle la phase a été mesurée, et {file} («\u{a0}{content}\u{a0}», type de contenu {content_type}) n'enregistre pas sa longueur de jauge. Audace n'écrit la longueur de jauge dans un fichier de phase qu'à partir de la version qui l'ajoute\u{a0}: {option} demande donc un enregistrement fait avec cette version ou une suivante\u{a0}; une phase MOZART ou MOZART_PLUS et un fichier converti sans elle n'en portent pas. Aucune longueur de jauge n'est supposée\u{a0}: retirez {option} pour afficher la phase en radians.";
    WStrainRateNoTrigger = "--strain-rate multiplies each line's strain increment by the trigger frequency, and {file} does not know its own (trigger frequency {hz} Hz). No rate is assumed: use --nanostrain to show the strain.",
        "--strain-rate multiplie l'incrément de déformation de chaque ligne par la fréquence de tir, et {file} ne connaît pas la sienne (fréquence de tir {hz} Hz). Aucune fréquence n'est supposée\u{a0}: utilisez --nanostrain pour afficher la déformation.";
    WUnitRefused = "{option}: {detail}", "{option} refusé par invisensing\u{a0}: {detail}";
    WEmptyTimeRange = "empty time range after --start-time/--duration (p0={p0}, p1={p1}, pulses={pulses}).",
        "plage de temps vide après --start-time/--duration (p0={p0}, p1={p1}, tirs={pulses}).";
    WEmptyDistanceRange = "empty spatial slice after --start-distance/--end-distance (d0={d0}, d1={d1}, positions={positions}).",
        "tranche de distance vide après --start-distance/--end-distance (d0={d0}, d1={d1}, positions={positions}).";
    WNoData = "no data read.", "aucune donnée lue.";
    WClipPercentileRange = "clip percentile must be between 0 and 100.",
        "le centile d'écrêtage doit être entre 0 et 100.";
    WEmptyPositionSpec = "empty position spec: '{spec}'.", "liste de positions vide\u{a0}: «\u{a0}{spec}\u{a0}».";
    WChannelUnknown = "unknown channel '{channel}'. Known: {known}",
        "canal inconnu «\u{a0}{channel}\u{a0}». Connus\u{a0}: {known}";
    WChannelUnavailable = "channel '{channel}' unavailable in mode {mode}. Available for this mode: {available}",
        "canal «\u{a0}{channel}\u{a0}» indisponible en mode {mode}. Disponibles pour ce mode\u{a0}: {available}";
    WColormapUnknown = "unknown colormap '{name}'.", "palette inconnue «\u{a0}{name}\u{a0}».";
    WWindowUnknown = "unknown window '{window}'. Known: {known}.",
        "fenêtre inconnue «\u{a0}{window}\u{a0}». Connues\u{a0}: {known}.";
    WBinsTooFew = "n_bins must be >= 1.", "le nombre de cellules doit être >= 1.";
    WPointsTooFew = "n_out must be >= 1.", "le nombre de points doit être >= 1.";
    WOptionInvalidValue = "argument {flag}: invalid {type} value: '{value}'",
        "argument {flag}\u{a0}: valeur {type} invalide\u{a0}: «\u{a0}{value}\u{a0}»";
    WOptionInvalidChoice = "argument {flag}: invalid choice: '{value}' (choose from {choices})",
        "argument {flag}\u{a0}: choix invalide\u{a0}: «\u{a0}{value}\u{a0}» (au choix\u{a0}: {choices})";
    WOptionNotAllowedWith = "argument {flag}: not allowed with argument {other}",
        "argument {flag}\u{a0}: incompatible avec l'argument {other}";
    WOptionRequired = "the following arguments are required: {flags}",
        "ces arguments sont obligatoires\u{a0}: {flags}";
    WWarnBandEdgeClamped = "band edge {edge} Hz exceeds Nyquist ({nyquist} Hz); clamping to Nyquist.",
        "le bord de bande {edge} Hz dépasse Nyquist ({nyquist} Hz)\u{a0}; il est ramené à Nyquist.";
    WWarnBandsMemory = "bandheatmaps: ~{gb} GB to load ({pulses} pulses x {bins} bins). Use --duration / --max-pulses / --max-space-bins if memory is limited.",
        "bandheatmaps\u{a0}: ~{gb} GB à charger ({pulses} tirs × {bins} cellules). Utilisez --duration / --max-pulses / --max-space-bins si la mémoire est limitée.";
    WWarnFftSubsampled = "FFT: {dropped} positions subsampled to {kept} (1 in {step}) to bound RAM. Restrict with --position-range or --duration for an exact average.",
        "FFT\u{a0}: {dropped} positions sous-échantillonnées à {kept} (1 sur {step}) pour borner la mémoire. Restreignez avec --position-range ou --duration pour une moyenne exacte.";
    WWarnFftMemory = "FFT: ~{gb} GB to load ({pulses} pulses x {positions} positions). Use --duration or --max-pulses if memory is limited.",
        "FFT\u{a0}: ~{gb} GB à charger ({pulses} tirs × {positions} positions). Utilisez --duration ou --max-pulses si la mémoire est limitée.";
    // Words the worker's parameters carry.
    TypeInt = "int", "entière";
    TypeFloat = "float", "décimale";

    // --- The CLI's fixed texts (`cli_text`) -----------------------------------
    // Quantities of the labels.
    QPhase = "phase", "phase";
    QStrain = "strain", "déformation";
    QStrainRate = "strain rate", "vitesse de déformation";
    QAmplitude = "amplitude", "amplitude";
    QAmplitudeCap = "Amplitude", "Amplitude";
    QScriptOutput = "script output", "sortie du script";
    QVariance = "variance", "variance";
    QSourceUnit = "source unit²", "unité source²";
    QFrontEndCount = "front-end count", "comptes du front-end";
    DbRef = "dB, ref={reference}", "dB, réf.\u{a0}{reference}";
    // Plot titles.
    TitleHeatmap = "{name} -- mode={mode}, channel={channel}", "{name} — mode={mode}, canal={channel}";
    TitleBands = "{name} -- band heatmaps (mode={mode}, channel={channel})",
        "{name} — cartes de bandes (mode={mode}, canal={channel})";
    PanelGlobalMean = "global (mean)", "globale (moyenne)";
    PanelGlobalWhole = "global (whole band)", "globale (bande entière)";
    PanelBand = "{low}-{high} Hz ({kind})", "{low}-{high} Hz ({kind})";
    KindRms = "RMS", "RMS";
    KindVariance = "variance", "variance";
    KindLogVariance = "log variance", "variance log";
    TitleFft = "Temporal FFT -- {name}\nmode={mode}, channel={channel}, {positions}, window={window}",
        "FFT temporelle — {name}\nmode={mode}, canal={channel}, {positions}, fenêtre={window}";
    FftAtPosition = "position={position} m", "position={position} m";
    FftAtPositions = "positions={positions} m", "positions={positions} m";
    FftRange = "range {from}-{to} m ({count} positions, averaged)",
        "plage {from}-{to} m ({count} positions, moyennées)";
    FftWhole = "whole fiber ({count} positions, averaged)", "toute la fibre ({count} positions, moyennées)";
    TitleTrace = "Time trace -- {name}\nchannel={channel}, position={position} m",
        "Trace temporelle — {name}\ncanal={channel}, position={position} m";
    TitleScope = "{name} -- {channel} (oscilloscope x{speed}, {rate} lines/s)",
        "{name} — {channel} (oscilloscope ×{speed}, {rate} lignes/s)";
    TitleInspect = "{name} -- {source}, location index {index} ({position} m)",
        "{name} — {source}, emplacement d'index {index} ({position} m)";
    SourceChannel = "channel={channel}", "canal={channel}";
    // The inspection's statistics.
    StatIndex = "Location index", "Index de l'emplacement";
    StatPosition = "Position", "Position";
    StatWindow = "Window", "Fenêtre";
    StatSamples = "Samples", "Échantillons";
    StatRate = "Rate", "Cadence";
    StatMean = "Mean", "Moyenne";
    StatStd = "Std", "Écart-type";
    StatMin = "Min", "Min";
    StatMax = "Max", "Max";
    // The `info` text: its keys, then its sentences.
    KFile = "File", "Fichier";
    KMode = "Mode", "Mode";
    KContentType = "Content type", "Type de contenu";
    KQuantity = "Quantity", "Grandeur";
    KSourceColumn = "Source column", "Colonne source";
    KFormatVersion = "Format version", "Version du format";
    KCard = "Card", "Carte";
    KOtherHalf = "Other half", "Autre moitié";
    KGaugeLength = "Gauge length", "Longueur de jauge";
    KStrain = "Strain", "Déformation";
    KStrainRate = "Strain rate", "Vitesse de déformation";
    KTimeGaps = "Time gaps", "Trous temporels";
    KTimestamp = "Timestamp", "Horodatage";
    KLastPulse = "Last pulse", "Dernier tir";
    KTime = "Time", "Temps";
    KPulses = "Pulses", "Tirs";
    KDuration = "Duration", "Durée";
    KSampleRate = "Sample rate", "Échantillonnage";
    KTrigFrequency = "Trig frequency", "Fréquence de tir";
    KLineSize = "Line size", "Taille de ligne";
    KPositions = "Positions", "Positions";
    KSpatialStep = "Spatial step", "Pas spatial";
    KLinePeriod = "Line period", "Période de ligne";
    KFiberLength = "Fiber length", "Longueur de fibre";
    KTriggerDelay = "Trigger delay", "Retard du tir";
    KAdcRange = "ADC range", "Plage de l'ADC";
    KSampleSize = "Sample size", "Taille d'échantillon";
    KSockets = "Sockets", "Sockets";
    KFlags = "Flags", "Indicateurs";
    KCompression = "Compression", "Compression";
    InfoNone = "(none)", "(aucun)";
    InfoFileDuration = "(file: {duration} s)", "(fichier\u{a0}: {duration} s)";
    InfoSamplesPerPulse = "{count} samples/pulse", "{count} échantillons/tir";
    InfoPerPulse = "{count} per pulse", "{count} par tir";
    InfoLineZeroNone = "line 0 has none (NaN)", "la ligne 0 n'en a pas (NaN)";
    InfoTimeGapsValue = "{count} line(s) after lost shots, NaN: {lines}",
        "{count} ligne(s) après des tirs perdus, NaN\u{a0}: {lines}";
    InfoStats = "Stats for channel '{channel}'{shown} over {count} pulses{note}:",
        "Statistiques du canal «\u{a0}{channel}\u{a0}»{shown} sur {count} tirs{note}\u{a0}:";
    InfoStatsNone = "Stats for channel '{channel}'{shown} over {count} pulses: no finite sample{note}",
        "Statistiques du canal «\u{a0}{channel}\u{a0}»{shown} sur {count} tirs\u{a0}: aucun échantillon fini{note}";
    InfoLeftOut = " ({count} NaN/inf samples left out)", " ({count} échantillons NaN/inf écartés)";
    InfoMean = "mean", "moyenne";
    InfoStd = "std", "écart-type";
    TrigActual = "actual {frequency} Hz", "réelle {frequency} Hz";
    CorrectionCount = "{tag}: count (no time map)", "{tag}\u{a0}: comptage (pas de table de temps)";
    CorrectionCorrected = "{tag}: corrected", "{tag}\u{a0}: corrigé";
    CorrectionVsCount = "{delta} ms vs the count", "{delta} ms par rapport au comptage";
    CorrectionStartTime = "start time", "heure de début";
    CompressionGrid = "phase on a {step} rad grid{strain}", "phase sur une grille de {step} rad{strain}";
    CompressionError = "error <= {error} rad{strain}", "erreur ≤ {error} rad{strain}";
}

/// Fills the `{name}` placeholders of `template`; `None` when one of them
/// has no value.
pub fn fill(template: &str, mut value: impl FnMut(&str) -> Option<String>) -> Option<String> {
    let mut out = String::with_capacity(template.len() + 16);
    let mut rest = template;
    while let Some(open) = rest.find('{') {
        out.push_str(&rest[..open]);
        let after = &rest[open + 1..];
        let close = after.find('}')?;
        out.push_str(&value(&after[..close])?);
        rest = &after[close + 1..];
    }
    out.push_str(rest);
    Some(out)
}

/// The placeholder names of `template`, in order.
#[cfg(test)]
pub fn placeholders(template: &str) -> Vec<&str> {
    let mut names = Vec::new();
    let mut rest = template;
    while let Some(open) = rest.find('{') {
        let after = &rest[open + 1..];
        let Some(close) = after.find('}') else { break };
        names.push(&after[..close]);
        rest = &after[close + 1..];
    }
    names
}

// --- Texts -------------------------------------------------------------------

/// A message of the worker: its English words and, when the worker attached
/// one, the code and the parameters the viewer renders in its language.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct WorkerText {
    pub text: String,
    pub code: Option<String>,
    pub params: BTreeMap<String, String>,
}

impl WorkerText {
    /// A worker text with no code (shown as written).
    pub fn plain(text: impl Into<String>) -> WorkerText {
        WorkerText {
            text: text.into(),
            ..WorkerText::default()
        }
    }
}

/// A text of the window, rendered in the language of the moment: a message
/// shown now and still on screen after a change of language follows it.
#[derive(Debug, Clone, PartialEq)]
pub enum Text {
    /// A catalogue entry and the values of its placeholders.
    Msg(Msg, Vec<(&'static str, Text)>),
    /// A message of the worker.
    Worker(WorkerText),
    /// Words that are not translated: a path, a command, an error of the
    /// operating system or of a library.
    Plain(String),
}

impl Text {
    /// The text in `lang`.
    pub fn render(&self, lang: Lang) -> String {
        match self {
            Text::Msg(msg, args) => fill(msg.text(lang), |name| {
                args.iter()
                    .find(|(n, _)| *n == name)
                    .map(|(_, v)| v.render(lang))
            })
            .unwrap_or_else(|| msg.text(lang).to_string()),
            Text::Worker(w) => render_worker(lang, w),
            Text::Plain(s) => s.clone(),
        }
    }

    /// Sets the value of the placeholder `name` of a catalogue entry.
    pub fn arg(mut self, name: &'static str, value: impl Into<Text>) -> Text {
        if let Text::Msg(_, args) = &mut self {
            args.push((name, value.into()));
        }
        self
    }

    /// `self`, then `detail` on the next lines (`message:\ndetail`).
    pub fn with_detail(self, detail: impl Into<Text>) -> Text {
        Text::from(Msg::WithDetail)
            .arg("message", self)
            .arg("detail", detail)
    }

    /// `self`, a blank line, then `then`.
    pub fn then(self, then: impl Into<Text>) -> Text {
        Text::from(Msg::Paragraphs)
            .arg("first", self)
            .arg("second", then)
    }
}

impl From<Msg> for Text {
    fn from(msg: Msg) -> Text {
        Text::Msg(msg, Vec::new())
    }
}

impl From<String> for Text {
    fn from(s: String) -> Text {
        Text::Plain(s)
    }
}

impl From<&str> for Text {
    fn from(s: &str) -> Text {
        Text::Plain(s.to_string())
    }
}

impl From<WorkerText> for Text {
    fn from(w: WorkerText) -> Text {
        Text::Worker(w)
    }
}

// --- Numbers -----------------------------------------------------------------

/// A number as the CLI or Python writes it (`-12.5`, `1,234,567`,
/// `75,007.5008`, `4.02e+03`, `+0.098`) the French way: a decimal comma and
/// a non-breaking space between thousands. `None` when `s` is not such a
/// number (a list, a word, `nan`).
pub fn french_number(s: &str) -> Option<String> {
    let (sign, rest) = match s.as_bytes().first() {
        Some(b'+' | b'-') => s.split_at(1),
        _ => ("", s),
    };
    let (mantissa, exponent) = match rest.find(['e', 'E']) {
        Some(i) => rest.split_at(i),
        None => (rest, ""),
    };
    let digits = |t: &str| !t.is_empty() && t.bytes().all(|b| b.is_ascii_digit());
    if !exponent.is_empty() && !digits(exponent[1..].trim_start_matches(['+', '-'])) {
        return None;
    }
    let (int, frac) = match mantissa.split_once('.') {
        Some((i, f)) => (i, Some(f)),
        None => (mantissa, None),
    };
    if frac.is_some_and(|f| !digits(f)) {
        return None;
    }
    let groups: Vec<&str> = int.split(',').collect();
    if !groups.iter().all(|g| digits(g)) {
        return None;
    }
    if groups.len() > 1 && (groups[0].len() > 3 || groups[1..].iter().any(|g| g.len() != 3)) {
        return None;
    }
    let mut out = String::from(sign);
    out.push_str(&groups.join(&NBSP.to_string()));
    if let Some(f) = frac {
        out.push(',');
        out.push_str(f);
    }
    out.push_str(exponent);
    Some(out)
}

/// A number written by the CLI, in `lang`: unchanged in English, the French
/// way in French; a text that is not a number is unchanged.
pub fn localize_number(lang: Lang, s: &str) -> String {
    match lang {
        Lang::En => s.to_string(),
        Lang::Fr => french_number(s).unwrap_or_else(|| s.to_string()),
    }
}

/// `x` with `sig` significant digits (`%g`), in `lang`.
pub fn num(lang: Lang, x: f64, sig: usize) -> String {
    localize_number(lang, &fmt_g(x, sig))
}

/// An integer with its thousands grouped: `1,234,567` / `1 234 567`.
pub fn count(lang: Lang, n: u64) -> String {
    let sep = match lang {
        Lang::En => ',',
        Lang::Fr => NBSP,
    };
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i).is_multiple_of(3) {
            out.push(sep);
        }
        out.push(c);
    }
    out
}

// --- The worker's codes ------------------------------------------------------

/// The catalogue entry of a worker message code (`gui_worker.py`).
pub fn worker_msg(code: &str) -> Option<Msg> {
    Some(match code {
        "cancelled" => Msg::WCancelled,
        "file_not_found" => Msg::WFileNotFound,
        "internal" => Msg::WInternal,
        "protocol_mismatch" => Msg::WProtocolMismatch,
        "scope_session_gone" => Msg::WScopeSessionGone,
        "unknown_command" => Msg::WUnknownCommand,
        "request_field" => Msg::WRequestField,
        "view_unknown" => Msg::WViewUnknown,
        "option_unknown" => Msg::WOptionUnknown,
        "option_takes_value" => Msg::WOptionTakesValue,
        "option_unsupported_value" => Msg::WOptionUnsupportedValue,
        "option_not_bool" => Msg::WOptionNotBool,
        "unit_not_offered" => Msg::WUnitNotOffered,
        "options_not_object" => Msg::WOptionsNotObject,
        "scope_unavailable" => Msg::WScopeUnavailable,
        "bands_need_f1" => Msg::WBandsNeedF1,
        "bands_f3_needs_f2" => Msg::WBandsF3NeedsF2,
        "bands_f0_negative" => Msg::WBandsF0Negative,
        "bands_not_increasing" => Msg::WBandsNotIncreasing,
        "bands_none_below_nyquist" => Msg::WBandsNoneBelowNyquist,
        "bands_too_few_pulses" => Msg::WBandsTooFewPulses,
        "bands_too_much_memory" => Msg::WBandsTooMuchMemory,
        "position_outside" => Msg::WPositionOutside,
        "trace_no_data" => Msg::WTraceNoData,
        "fft_too_few_pulses" => Msg::WFftTooFewPulses,
        "fft_too_few_samples" => Msg::WFftTooFewSamples,
        "scope_needs_invisensing" => Msg::WScopeNeedsInvisensing,
        "index_outside" => Msg::WIndexOutside,
        "inspect_too_few_pulses" => Msg::WInspectTooFewPulses,
        "unit_needs_invisensing" => Msg::WUnitNeedsInvisensing,
        "unit_not_a_phase" => Msg::WUnitNotAPhase,
        "unit_no_gauge_length" => Msg::WUnitNoGaugeLength,
        "strain_rate_no_trigger" => Msg::WStrainRateNoTrigger,
        "unit_refused" => Msg::WUnitRefused,
        "empty_time_range" => Msg::WEmptyTimeRange,
        "empty_distance_range" => Msg::WEmptyDistanceRange,
        "no_data" => Msg::WNoData,
        "clip_percentile_range" => Msg::WClipPercentileRange,
        "empty_position_spec" => Msg::WEmptyPositionSpec,
        "channel_unknown" => Msg::WChannelUnknown,
        "channel_unavailable" => Msg::WChannelUnavailable,
        "colormap_unknown" => Msg::WColormapUnknown,
        "window_unknown" => Msg::WWindowUnknown,
        "bins_too_few" => Msg::WBinsTooFew,
        "points_too_few" => Msg::WPointsTooFew,
        "option_invalid_value" => Msg::WOptionInvalidValue,
        "option_invalid_choice" => Msg::WOptionInvalidChoice,
        "option_not_allowed_with" => Msg::WOptionNotAllowedWith,
        "option_required" => Msg::WOptionRequired,
        "warn_band_edge_clamped" => Msg::WWarnBandEdgeClamped,
        "warn_bands_memory" => Msg::WWarnBandsMemory,
        "warn_fft_subsampled" => Msg::WWarnFftSubsampled,
        "warn_fft_memory" => Msg::WWarnFftMemory,
        _ => return None,
    })
}

/// Parameters of the worker's codes that hold a number (written the French
/// way in French) or a list of numbers.
const NUMERIC_PARAMS: &[&str] = &[
    "position",
    "max",
    "index",
    "last",
    "gb",
    "pulses",
    "bins",
    "edge",
    "edges",
    "nyquist",
    "hz",
    "p0",
    "p1",
    "d0",
    "d1",
    "positions",
    "dropped",
    "kept",
    "step",
];

/// A parameter of a worker code in `lang`: numbers the French way, a list
/// of numbers separated by semicolons, the quantity and type words
/// translated; anything else as the worker wrote it.
fn localize_param(lang: Lang, name: &str, value: &str) -> String {
    if lang == Lang::En {
        return value.to_string();
    }
    match name {
        "what" => crate::cli_text::quantity_head(lang, value),
        "type" => match value {
            "int" => lang.t(Msg::TypeInt).to_string(),
            "float" => lang.t(Msg::TypeFloat).to_string(),
            other => other.to_string(),
        },
        n if NUMERIC_PARAMS.contains(&n) => {
            if let Some(v) = french_number(value) {
                return v;
            }
            let items: Option<Vec<String>> = value.split(", ").map(french_number).collect();
            match items {
                Some(items) if items.len() > 1 => items.join(&format!("{NBSP}; ")),
                _ => value.to_string(),
            }
        }
        _ => value.to_string(),
    }
}

/// A worker message in `lang`: its own English words in English; in French
/// the catalogue entry of its code filled with its parameters, or its
/// English words when the code is unknown or a parameter is missing.
pub fn render_worker(lang: Lang, w: &WorkerText) -> String {
    if lang == Lang::En {
        return w.text.clone();
    }
    w.code
        .as_deref()
        .and_then(worker_msg)
        .and_then(|msg| {
            fill(msg.text(lang), |name| {
                w.params.get(name).map(|v| localize_param(lang, name, v))
            })
        })
        .unwrap_or_else(|| w.text.clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;

    fn sorted(names: Vec<&str>) -> BTreeSet<&str> {
        names.into_iter().collect()
    }

    /// The Dashboard's rule, here too: every message has every language.
    #[test]
    fn every_message_has_its_english_and_its_french() {
        let mut ids = BTreeSet::new();
        for &m in Msg::ALL {
            assert!(ids.insert(m.id()), "{} twice", m.id());
            for lang in Lang::ALL {
                let t = m.text(lang);
                assert!(
                    !t.trim().is_empty(),
                    "{} has no {} text",
                    m.id(),
                    lang.code()
                );
                assert_eq!(
                    t,
                    t.trim_end(),
                    "{}: trailing blank in {}",
                    m.id(),
                    lang.code()
                );
            }
            let (en, fr) = (m.text(Lang::En), m.text(Lang::Fr));
            assert_eq!(
                sorted(placeholders(en)),
                sorted(placeholders(fr)),
                "{}: the placeholders differ between English and French",
                m.id()
            );
            for name in placeholders(en) {
                assert!(
                    !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_'),
                    "{}: bad placeholder {{{name}}}",
                    m.id()
                );
            }
            assert_eq!(
                en.matches('{').count(),
                en.matches('}').count(),
                "{}: unbalanced braces",
                m.id()
            );
        }
    }

    #[test]
    fn the_french_follows_french_typography() {
        for &m in Msg::ALL {
            let fr = m.text(Lang::Fr);
            let chars: Vec<char> = fr.chars().collect();
            for (i, &c) in chars.iter().enumerate() {
                if matches!(c, ':' | ';' | '?' | '!') && i > 0 {
                    assert_ne!(
                        chars[i - 1],
                        ' ',
                        "{}: a plain space before '{c}' in {fr:?} (use \\u{{a0}})",
                        m.id()
                    );
                }
                if c == '«' {
                    assert_eq!(
                        chars.get(i + 1),
                        Some(&NBSP),
                        "{}: « then U+00A0 in {fr:?}",
                        m.id()
                    );
                }
                if c == '»' {
                    assert_eq!(chars[i - 1], NBSP, "{}: U+00A0 then » in {fr:?}", m.id());
                }
            }
            assert!(
                !fr.contains('"'),
                "{}: French quotes are « », not \" in {fr:?}",
                m.id()
            );
            assert!(
                !fr.contains('\u{202f}'),
                "{}: the default fonts have no U+202F",
                m.id()
            );
        }
    }

    #[test]
    fn option_names_in_english_are_the_cli_dests_made_readable() {
        let dests = [
            ("channel", Msg::OptChannel),
            ("start_time", Msg::OptStartTime),
            ("max_space_bins", Msg::OptMaxSpaceBins),
            ("fft_log", Msg::OptFftLog),
            ("db", Msg::OptDb),
        ];
        for (dest, msg) in dests {
            assert_eq!(crate::params::humanize(dest), msg.text(Lang::En));
        }
    }

    #[test]
    fn the_language_comes_from_the_locale() {
        assert_eq!(Lang::from_locale(Some("fr-FR")), Lang::Fr);
        assert_eq!(Lang::from_locale(Some("FR_ca.UTF-8")), Lang::Fr);
        assert_eq!(Lang::from_locale(Some("en-US")), Lang::En);
        assert_eq!(Lang::from_locale(Some("de-DE")), Lang::En);
        assert_eq!(Lang::from_locale(Some("f")), Lang::En);
        assert_eq!(Lang::from_locale(None), Lang::En);
        assert_eq!(Lang::parse("FR"), Some(Lang::Fr));
        assert_eq!(Lang::parse("en"), Some(Lang::En));
        assert_eq!(Lang::parse("de"), None);
    }

    #[test]
    fn texts_fill_their_placeholders_in_each_language() {
        let t = Text::from(Msg::CannotOpen).arg(
            "reason",
            Text::from(Msg::PathError)
                .arg("path", "/d/x.h5")
                .arg("error", "denied"),
        );
        assert_eq!(t.render(Lang::En), "Cannot open the file: /d/x.h5: denied");
        assert_eq!(
            t.render(Lang::Fr),
            "Impossible d'ouvrir le fichier\u{a0}: /d/x.h5\u{a0}: denied"
        );
        // A missing value leaves the entry as written, never a half sentence.
        assert_eq!(
            Text::from(Msg::CannotOpen).render(Lang::En),
            "Cannot open the file: {reason}"
        );
        assert_eq!(
            Lang::Fr.f(Msg::Runs, &[("command", "py")]),
            "Exécute\u{a0}: py"
        );
    }

    #[test]
    fn numbers_are_written_the_french_way() {
        let n = |s| french_number(s);
        assert_eq!(n("12.5").as_deref(), Some("12,5"));
        assert_eq!(n("-0.4037").as_deref(), Some("-0,4037"));
        assert_eq!(n("+0.098").as_deref(), Some("+0,098"));
        assert_eq!(n("75,007.5008").as_deref(), Some("75\u{a0}007,5008"));
        assert_eq!(n("62,500,000").as_deref(), Some("62\u{a0}500\u{a0}000"));
        assert_eq!(n("4.02e+03").as_deref(), Some("4,02e+03"));
        assert_eq!(n("1.5e-5").as_deref(), Some("1,5e-5"));
        assert_eq!(n("20000").as_deref(), Some("20000"));
        assert_eq!(n("9.6,19.2"), None, "a list is not a number");
        assert_eq!(n("1,23"), None);
        assert_eq!(n("nan"), None);
        assert_eq!(n("phase"), None);
        assert_eq!(n(""), None);
        assert_eq!(count(Lang::En, 1_234_567), "1,234,567");
        assert_eq!(count(Lang::Fr, 1_234_567), "1\u{a0}234\u{a0}567");
        assert_eq!(count(Lang::Fr, 999), "999");
        assert_eq!(num(Lang::Fr, 2.795755, 4), "2,796");
        assert_eq!(num(Lang::En, 2.795755, 4), "2.796");
    }

    fn worker(text: &str, code: &str, params: &[(&str, &str)]) -> WorkerText {
        WorkerText {
            text: text.into(),
            code: Some(code.into()),
            params: params
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect(),
        }
    }

    #[test]
    fn worker_messages_render_by_code_in_french_and_as_written_in_english() {
        let w = worker(
            "position 9000.0 m outside the fiber (range: 0 to 1278.40 m).",
            "position_outside",
            &[("position", "9000.0"), ("max", "1278.40")],
        );
        assert_eq!(render_worker(Lang::En, &w), w.text);
        assert_eq!(
            render_worker(Lang::Fr, &w),
            "position 9000,0 m hors de la fibre (plage\u{a0}: 0 à 1278,40 m)."
        );
        let edges = worker(
            "band edges must be strictly increasing, got [0.0, 20.0, 10.0] Hz.",
            "bands_not_increasing",
            &[("edges", "0.0, 20.0, 10.0")],
        );
        assert_eq!(
            render_worker(Lang::Fr, &edges),
            "les bords des bandes doivent être strictement croissants, reçu [0,0\u{a0}; 20,0\u{a0}; 10,0] Hz."
        );
        let unit = worker(
            "x",
            "unit_not_a_phase",
            &[
                ("option", "--nanostrain"),
                ("what", "strain"),
                ("unit", "nm/m"),
                ("file", "raw.dat"),
                ("content", "raw"),
                ("content_type", "0"),
                ("mode", "raw"),
            ],
        );
        assert!(
            render_worker(Lang::Fr, &unit)
                .starts_with("--nanostrain affiche une phase en déformation (nm/m), et raw.dat")
        );
        // Unknown code, or a parameter missing: the English words.
        assert_eq!(
            render_worker(Lang::Fr, &worker("w", "no_such_code", &[])),
            "w"
        );
        assert_eq!(
            render_worker(Lang::Fr, &worker("w", "position_outside", &[])),
            "w"
        );
        assert_eq!(
            render_worker(Lang::Fr, &WorkerText::plain("as is")),
            "as is"
        );
    }

    /// Every code the installed worker can send has its entry, and every
    /// placeholder of the entry is a parameter the worker sends with it.
    /// Set `AUDACE_GUI_TEST_PYTHON` to an interpreter that imports
    /// audace_display (as for the worker test).
    #[test]
    fn every_worker_code_has_its_entry_when_a_worker_is_configured() {
        let Ok(python) = std::env::var("AUDACE_GUI_TEST_PYTHON") else {
            eprintln!("AUDACE_GUI_TEST_PYTHON is not set: skipped");
            return;
        };
        let words = crate::worker::split_command(&python).expect("interpreter command");
        let out = std::process::Command::new(&words[0])
            .args(&words[1..])
            .args([
                "-c",
                "import json; from audace_display import gui_worker; \
                 print(json.dumps(gui_worker.message_codes()))",
            ])
            .output()
            .expect("run the interpreter");
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let codes: BTreeMap<String, Vec<String>> =
            serde_json::from_slice(&out.stdout).expect("message_codes() as JSON");
        assert!(codes.len() > 40, "{codes:?}");
        for (code, params) in &codes {
            let msg = worker_msg(code).unwrap_or_else(|| panic!("no entry for the code {code}"));
            for lang in Lang::ALL {
                for name in placeholders(msg.text(lang)) {
                    assert!(
                        params.iter().any(|p| p == name),
                        "{code}: {{{name}}} is not one of its parameters {params:?}"
                    );
                }
            }
        }
    }
}
