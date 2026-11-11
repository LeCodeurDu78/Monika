// Interface language for the page. The backend owns the language setting; this module translates
// the strings already present in the DOM (static HTML and rendered panels) without touching user
// content. The original text is remembered per node, so switching back to French restores it
// exactly. Unknown text is left as it is.

const EN = {
  // Shell
  'Au repos': 'Idle',
  'Connexion…': 'Connecting…',
  'Connectée': 'Connected',
  'Connexion perdue : nouvelle tentative…': 'Connection lost: retrying…',
  'Réglages': 'Settings',
  'Chat': 'Chat',
  'Tableau de bord': 'Dashboard',
  'Sections': 'Sections',
  'Avatar': 'Avatar',
  'Avatar 3D de Monika': 'Monika 3D avatar',
  'Redimensionner le panneau': 'Resize the panel',
  'Masquer le panneau': 'Hide the panel',
  'Afficher le panneau': 'Show the panel',
  'Conversation': 'Conversation',
  'Contexte': 'Context',
  'Relation': 'Relationship',
  'Mémoire': 'Memory',
  'Sessions': 'Sessions',
  'Recettes': 'Recipes',
  'Outils perso': 'Custom tools',
  'Chargement…': 'Loading…',
  'Chargement de Monika…': 'Loading Monika…',
  'Téléchargement du modèle…': 'Downloading the model…',
  'Je réfléchis': 'Thinking',
  'J’écoute': 'Listening',
  'Je parle': 'Speaking',
  "aujourd'hui": 'today',
  'demain': 'tomorrow',
  'Rien à afficher.': 'Nothing to show.',
  'Démarrage en cours…': 'Starting…',

  // Settings page
  'Monika — réglages': 'Monika — settings',
  'Retour à Monika': 'Back to Monika',
  'Santé': 'Health',
  'Fournisseur': 'Provider',
  "OpenRouter, Ollama ou OpenAI Custom (une API compatible OpenAI). Le choix remplit l'adresse et le modèle ; en OpenAI Custom, saisis l'adresse toi-même. La clé est écrite dans .env et demande un redémarrage.":
    'OpenRouter, Ollama or OpenAI Custom (an OpenAI-compatible API). The choice fills the address and the model; with OpenAI Custom, enter the address yourself. The key is written to .env and needs a restart.',
  'URL de base (OpenAI Custom)': 'Base URL (OpenAI Custom)',
  "Adresse d'une API compatible OpenAI, généralement terminée par /v1. Ignorée pour OpenRouter et Ollama.":
    'OpenAI-compatible API address, usually ending in /v1. Ignored for OpenRouter and Ollama.',
  "Indique l'URL de base du fournisseur OpenAI Custom.": "Enter the OpenAI Custom provider's base URL.",
  'URL de base requise pour le fournisseur OpenAI Custom.': 'Base URL required for the OpenAI Custom provider.',
  'Clé API du fournisseur (jamais renvoyée)': 'Provider API key (never sent back)',
  'Appliquer': 'Apply',
  'Tester maintenant': 'Test now',
  'Sondes en cours…': 'Probing…',
  'Enregistrer': 'Save',
  'Recharger': 'Reload',
  'figé par .env': 'pinned by .env',
  'Rien à enregistrer.': 'Nothing to save.',
  'Enregistré.': 'Saved.',
  'Monika ne répond pas.': 'Monika is not responding.',
  'Langue': 'Language',
  'Comportement': 'Behaviour',
  'Personnage': 'Character',
  'Rôles': 'Roles',
  'Tâches planifiées': 'Scheduled tasks',
  'Briefing du matin': 'Morning briefing',
  'Veille nocturne': 'Nightly review',
  'Documents': 'Documents',
  'Voix': 'Voice',
  'Page et journaux': 'Page and logs',
  'Sondé en': 'Probed in',
  'rafraîchi toutes les 5 s quand cet onglet est visible.':
    'refreshed every 5 s while this tab is visible.',
  'désactivé': 'disabled',
  'désactivée': 'disabled',
  'désactivée (mode texte)': 'disabled (text mode)',
  'aucun serveur configuré': 'no server configured',
  'Mémoire rapide': 'Fast memory',
  'Reconnaissance vocale': 'Speech recognition',
  'Serveurs MCP': 'MCP servers',
  'Page locale': 'Local page',
  'Voix (GPT-SoVITS)': 'Voice (GPT-SoVITS)',
  'Un fichier mcp_servers.json modifié est rechargé automatiquement.':
    'An edited mcp_servers.json is reloaded automatically.',
  'Aucun serveur MCP configuré.': 'No MCP server configured.',
  'Enregistrés dans': 'Saved in',
  'Vide : garde la clé actuelle.': 'Empty: keep the current key.',
  'Langue appliquée.': 'Language applied.',
  'Fournisseur appliqué.': 'Provider applied.',
  'Fournisseur appliqué. Redémarre Monika :': 'Provider applied. Restart Monika:',

  // Settings groups and fields
  'Général': 'General',
  'Modèle': 'Model',
  'Mémoire et documents': 'Memory and documents',
  'Automatisations': 'Automations',
  'Avancé': 'Advanced',
  'Ton prénom': 'Your first name',
  'Consignes de comportement': 'Behaviour instructions',
  'Température': 'Temperature',
  'Délai maximal': 'Timeout',
  "Appels d'outils par tour": 'Tool calls per turn',
  'Synthèse vocale': 'Speech synthesis',
  'Parler en mode texte': 'Speak in text mode',
  'Vitesse de la voix': 'Voice speed',
  'Émotions dans la voix': 'Emotions in the voice',
  'Pauses': 'Pauses',
  'Longueur des pauses': 'Pause length',
  'Langue parlée': 'Spoken language',
  'Mémoriser automatiquement': 'Remember automatically',
  'Consolidation': 'Consolidation',
  'Passages cités': 'Quoted passages',
  'Seuil de pertinence': 'Relevance threshold',
  'Reclassement': 'Reranking',
  'OCR des PDF scannés': 'Scanned PDF OCR',
  'Émotions et relation': 'Emotions and relationship',
  'Lire le ton': 'Read the tone',
  'Heure du briefing': 'Briefing time',
  'Ville (météo)': 'City (weather)',
  'Heure de la veille': 'Review time',
  'Niveau de journal': 'Log level',
  'Budget de contexte': 'Context budget',
  'Auto': 'Auto',
  'Toujours': 'Always',
  'Jamais': 'Never',
  'Neutre': 'Neutral',
  'Naturel': 'Natural',
  'Le prénom que Monika utilise pour s’adresser à toi.': 'The name Monika uses for you.',
  'Ajoutées à son prompt : ton, habitudes, ce qu’elle doit éviter.':
    'Added to her prompt: tone, habits, what she must avoid.',
  'Le modèle servi par ton fournisseur.': 'The model served by your provider.',
  'Plus bas = plus stable, plus haut = plus créatif.':
    'Lower is steadier, higher is more creative.',
  'Au-delà, l’appel au modèle est abandonné (secondes).':
    'Past this, the model call is abandoned (seconds).',
  'Nombre maximal d’outils utilisés dans un tour.': 'Maximum tools used in one turn.',
  'GPT-SoVITS doit tourner sur la machine.': 'GPT-SoVITS must be running on the machine.',
  'Fait parler Monika même quand tu écris dans la page.':
    'Makes Monika speak even when you type on the page.',
  '1 = vitesse normale.': '1 = normal speed.',
  'Auto n’utilise que les émotions enregistrées dans ta voix.':
    'Auto only uses the emotions recorded in your voice.',
  'Neutre n’ajoute aucun silence entre les phrases.':
    'Neutral adds no silence between sentences.',
  'Multiplie les pauses calculées.': 'Multiplies the computed pauses.',
  'small est un bon compromis ; medium et large sont plus lents.':
    'small is a good compromise; medium and large are slower.',
  'Langue de ton micro ; auto laisse Whisper la deviner.':
    'Language of your microphone; auto lets Whisper guess it.',
  'Retient les faits cités pendant la conversation.':
    'Keeps the facts quoted during the conversation.',
  'Réécrit le carnet tous les N tours (minimum 2).':
    'Rewrites the notebook every N turns (minimum 2).',
  'Nombre de passages envoyés au modèle.': 'Number of passages sent to the model.',
  'Sous ce score, un passage est écarté (0 = tout garder).':
    'Below this score a passage is dropped (0 = keep everything).',
  'Reclasse les passages avant de répondre.': 'Reranks the passages before answering.',
  'Lit le texte des pages sans couche texte (RapidOCR, CPU).':
    'Reads text from pages without a text layer (RapidOCR, CPU).',
  'Humeur, favorabilité et gestes de l’avatar.':
    'Mood, favour and avatar gestures.',
  'Petit appel au modèle ; le lexique reste le repli.':
    'A small model call; the lexicon remains the fallback.',
  'Elle écrit quelques notes sur toi en arrière-plan.':
    'She writes a few notes about you in the background.',
  'Rappels et tâches récurrentes, tant que Monika tourne.':
    'Reminders and recurring tasks while Monika runs.',
  'Météo, agenda, mails et tâches une fois par jour.':
    'Weather, calendar, mail and tasks once a day.',
  'Heure locale, HH:MM.': 'Local time, HH:MM.',
  'Vide : pas de météo dans le briefing.': 'Empty: no weather in the briefing.',
  'Une note de la journée et des idées pour le lendemain.':
    'A note about the day and ideas for tomorrow.',
  'DEBUG, INFO, WARNING, ERROR ou CRITICAL.': 'DEBUG, INFO, WARNING, ERROR or CRITICAL.',
  'Au-delà, les anciens messages sont résumés.':
    'Past this, older messages are summarised.',

  // Panels and actions
  'Modifier': 'Edit',
  'Supprimer': 'Delete',
  'Annuler': 'Cancel',
  'Reprendre': 'Resume',
  'Repartir de zéro': 'Start over',
  'Oublier': 'Forget',
  'Effacer tout le journal': 'Clear the whole journal',
  'Journal de Monika': 'Monika’s journal',
  'Impression d’ensemble': 'Overall impression',
  'À reprendre': 'To pick up',
  'Pas de note pour l’instant.': 'No note yet.',
  'Mon journal est encore vide : j’écris une note tous les quelques échanges.':
    'My journal is still empty: I write a note every few exchanges.',
  'Journal désactivé (JOURNAL_ENABLED).': 'Journal disabled (JOURNAL_ENABLED).',
  'Mémoire désactivée ou indisponible.': 'Memory disabled or unavailable.',
  'Je ne sais encore rien de toi.': 'I don’t know anything about you yet.',
  'Aucune session enregistrée pour l’instant.': 'No saved session yet.',
  'Aucune recette : j’en propose une après un tour réussi d’au moins trois outils.':
    'No recipe yet: I propose one after a successful turn with at least three tools.',
  'Aucun outil perso : je n’ai encore rien créé de moi-même. Demande-moi une action qui manque à mes outils.':
    'No custom tool yet: I have not created anything myself. Ask me for an action my tools lack.',
  'Personnage désactivé (CHARACTER_ENABLED).': 'Character disabled (CHARACTER_ENABLED).',
  'Humeur : égale': 'Mood: even',
  'favorabilité': 'favour',
  'en cours': 'current',
  'résumé': 'summary',
  'brouillon, à garder': 'draft, to keep',
  'active': 'active',
  'proposée': 'proposed',
  'usage(s)': 'use(s)',
  'réussite(s)': 'success(es)',
  'note(s)': 'note(s)',
  'session(s)': 'session(s)',
  'recette(s)': 'recipe(s)',
  'outil(s) perso': 'custom tool(s)',
  'message(s)': 'message(s)',
  'jour(s) de présence': 'day(s) present',
  'série en cours': 'current streak',
  'entrée(s) dans mon carnet': 'entry(ies) in my notebook',
  'fait(s) en mémoire': 'fact(s) in memory',
  'version corrigée': 'corrected version',
  'note manuelle': 'manual note',
  'promesse': 'promise',
  'tour(s)': 'turn(s)',
  'code modifié, à revérifier': 'code changed, recheck',
  'mon carnet': 'my notebook',
  'sujet en cours': 'open topic',
  'épisode passé': 'past episode',

  // Confirmations
  'Reprendre la session': 'Resume session',
  'La conversation en cours sera remplacée.': 'The current conversation will be replaced.',
  'Ses messages et ses appels d’outils seront effacés.':
    'Its messages and tool calls will be erased.',
  'Remettre la relation à zéro (niveau, favorabilité et humeur) ?':
    'Reset the relationship (level, favour and mood)?',
  'Effacer toutes les notes, l’impression d’ensemble et les sujets à reprendre ?':
    'Erase all notes, the overall impression and the topics to pick up?',
  'Session reprise.': 'Session resumed.',

};

const PATTERNS = [
  [/^v(\d+) active · v(\d+) proposée$/, 'v$1 active · v$2 proposed'],
  [/^v(\d+) active$/, 'v$1 active'],
  [/^(\d+) active · v(\d+) proposée · (.+)$/, '$1 active · v$2 proposed · $3'],
  [/^favorabilité (.+) %$/, 'favour $1%'],
  [/^Humeur : (.+)$/, 'Mood: $1'],
  [/^(.+) tour\(s\)$/, '$1 turn(s)'],
  [/^(.+) message\(s\) · (.+) jour\(s\) de présence · série en cours : (.+)$/, '$1 message(s) · $2 day(s) present · current streak: $3'],
  [/^(.+) entrée\(s\) dans mon carnet$/, '$1 entry(ies) in my notebook'],
  [/^(.+) note\(s\)$/, '$1 note(s)'],
  [/^(.+) fait[(]s[)] en mémoire$/, '$1 fact(s) in memory'],
  [/^(.+) session\(s\)$/, '$1 session(s)'],
  [/^(.+) recette\(s\)$/, '$1 recipe(s)'],
  [/^(.+) outil\(s\) perso$/, '$1 custom tool(s)'],
  [/^(.+) tokens sur un budget de (.+)$/, '$1 tokens of a $2 budget'],
  [/^(.+) message\(s\) ; fenêtre du modèle : (.+) tokens$/, '$1 message(s); model window: $2 tokens'],
  [/^Sondé en (.+) ms · rafraîchi toutes les 5 s quand cet onglet est visible\.$/, 'Probed in $1 ms · refreshed every 5 s while this tab is visible.'],
  [/^Redémarre Monika pour appliquer : (.+)\.$/, 'Restart Monika to apply: $1.'],
  [/^Fournisseur appliqué\. Redémarre Monika : (.+)\.$/, 'Provider applied. Restart Monika: $1.'],
];

let language = 'fr';

// Original text per node, plus what this module last wrote: a new value typed by the app becomes
// the new original, so switching back to French restores the latest French text.
const TEXTS = new WeakMap();
const ATTRIBUTES = new WeakMap();
const TITLE = { source: null, applied: null };

function translate(value) {
  if (language !== 'en') return value;
  const direct = EN[value];
  if (direct !== undefined) return direct;
  for (const [pattern, replacement] of PATTERNS) {
    if (pattern.test(value)) return value.replace(pattern, replacement);
  }
  return value;
}

/** Translate one string, keeping the French original when switching back. */
function translated(source) {
  return language === 'en' ? translate(source) : source;
}

function applyText(node) {
  const current = node.nodeValue;
  if (!current || !current.trim()) return;   // whitespace-only nodes carry nothing to translate
  let state = TEXTS.get(node);
  if (!state || current !== state.applied) {
    state = { source: current, applied: current };
    TEXTS.set(node, state);
  }
  const source = state.source;
  const leading = source.match(/^\s*/)[0];
  const trailing = source.match(/\s*$/)[0];
  const core = source.slice(leading.length, source.length - trailing.length);
  const next = leading + translated(core) + trailing;
  state.applied = next;
  if (current !== next) node.nodeValue = next;
}

function applyAttribute(element, attribute) {
  const current = element.getAttribute(attribute);
  if (current === null) return;
  let store = ATTRIBUTES.get(element);
  if (!store) {
    store = new Map();
    ATTRIBUTES.set(element, store);
  }
  let state = store.get(attribute);
  if (!state || current !== state.applied) {
    state = { source: current, applied: current };
    store.set(attribute, state);
  }
  const next = translated(state.source);
  state.applied = next;
  if (current !== next) element.setAttribute(attribute, next);
}

function applyTo(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    applyText(node);
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;
  for (const attribute of ['title', 'aria-label', 'placeholder']) applyAttribute(node, attribute);
  for (const child of node.childNodes) applyTo(child);
}

function applyTitle() {
  const current = document.title;
  if (TITLE.source === null || current !== TITLE.applied) TITLE.source = current;
  TITLE.applied = translated(TITLE.source);
  if (current !== TITLE.applied) document.title = TITLE.applied;
}

export function setLanguage(code) {
  language = code === 'en' ? 'en' : 'fr';
  document.documentElement.lang = language;
  applyTo(document.body);
  applyTitle();
}

export function t(french) {
  return translate(french);
}

export function currentLanguage() {
  return language;
}

export function startI18n() {
  const requested = new URLSearchParams(location.search).get('lang');
  if (requested) setLanguage(requested);
  if (window.__monikaI18n) return;
  window.__monikaI18n = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'characterData') applyText(mutation.target);
      for (const node of mutation.addedNodes || []) applyTo(node);
    }
  });
  window.__monikaI18n.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });
}
