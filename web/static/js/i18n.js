// Interface language for the page. The backend owns the language setting; this module only
// translates the strings already present in the DOM (static HTML and rendered panels) without
// touching user content. Unknown text is left exactly as it is.

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
  'Sondes en cours…': 'Probing…',
  'Tester maintenant': 'Test now',
  'Les réglages grisés sont figés par le fichier .env du projet : ils se modifient à la main. Les autres sont enregistrés dans settings.json.':
    'Greyed settings are pinned by the project .env file: edit them by hand. The others are saved in settings.json.',
  'Enregistrer': 'Save',
  'Recharger': 'Reload',
  'Mots sensibles': 'Sensitive words',
  "Un mot par ligne. mild : rien (au plus une moue). bad : ton posé et moue. severe : ton posé, désengagement et une ligne d'audit. Aucun mot n'est livré avec le projet ; accents, majuscules et leetspeak sont normalisés.":
    'One word per line. mild: nothing (at most a pout). bad: composed tone and a pout. severe: composed tone, disengagement and an audit line. No word ships with the project; accents, capitals and leetspeak are normalized.',
  'Enregistrer la liste': 'Save the list',
  'Clé API': 'API key',
  'Nouvelle clé OpenAI (une seule ligne)': 'New OpenAI key (one line only)',
  'Enregistrer la clé': 'Save the key',
  "La valeur n'est jamais renvoyée par la page. Elle remplace la ligne OPENAI_API_KEY du .env : redémarrage requis.":
    'The value is never sent back by the page. It replaces the OPENAI_API_KEY line in .env: restart required.',
  'figé par .env': 'pinned by .env',
  'Rien à enregistrer.': 'Nothing to save.',
  'Enregistré.': 'Saved.',
  'La clé est vide.': 'The key is empty.',
  'Clé enregistrée.': 'Key saved.',
  'Clé enregistrée dans le .env : redémarre-moi pour qu’elle soit utilisée.':
    'Key saved in .env: restart me for it to be used.',
  'Une clé est configurée (sa valeur n’est jamais affichée).':
    'A key is configured (its value is never shown).',
  'Aucune clé configurée : le modèle ne répondra pas.':
    'No key configured: the model will not answer.',
  'Monika ne répond pas.': 'Monika is not responding.',
  'Modèle et génération': 'Model and generation',
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
  'Sessions désactivées (SESSIONS_ENABLED).': 'Sessions disabled (SESSIONS_ENABLED).',
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
];

let language = 'fr';

function translate(value) {
  if (language !== 'en') return value;
  const direct = EN[value];
  if (direct !== undefined) return direct;
  for (const [pattern, replacement] of PATTERNS) {
    if (pattern.test(value)) return value.replace(pattern, replacement);
  }
  return value;
}

function applyTo(node) {
  if (node.nodeType === Node.TEXT_NODE) {
    const value = node.nodeValue;
    if (!value || !value.trim()) return;
    const leading = value.match(/^\s*/)?.[0] || '';
    const trailing = value.match(/\s*$/)?.[0] || '';
    const core = value.slice(leading.length, value.length - trailing.length);
    const translated = translate(core);
    if (translated !== core) node.nodeValue = leading + translated + trailing;
    return;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return;
  for (const attr of ['title', 'aria-label', 'placeholder']) {
    const value = node.getAttribute(attr);
    if (!value) continue;
    const translated = translate(value);
    if (translated !== value) node.setAttribute(attr, translated);
  }
  for (const child of node.childNodes) applyTo(child);
}

export function setLanguage(code) {
  const next = code === 'en' ? 'en' : 'fr';
  if (next === language && document.documentElement.lang === next) {
    applyTo(document.body);
    return;
  }
  language = next;
  document.documentElement.lang = next;
  applyTo(document.body);
}

export function t(french) {
  return translate(french);
}

export function currentLanguage() {
  return language;
}

export function startI18n() {
  if (window.__monikaI18n) return;
  window.__monikaI18n = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'characterData') applyTo(mutation.target);
      for (const node of mutation.addedNodes || []) applyTo(node);
    }
  });
  window.__monikaI18n.observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
  });
}
