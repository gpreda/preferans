import google.generativeai as genai
import time
import json
import os
import re

DEBUG = False
GEMINI_MODEL = 'gemini-2.0-flash'
LANGUAGE = 'Spanish'
MIN_DIFFICULTY = 1
MAX_DIFFICULTY = 10
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tongue_state.json')
CONFIG_FILE = os.path.expanduser('~/.config/tongue/config.json')

# Advancement criteria
ADVANCE_WINDOW_SIZE = 5       # Number of recent scores to consider
ADVANCE_SCORE_THRESHOLD = 80  # Minimum score to count as "good"
ADVANCE_REQUIRED_GOOD = 4     # Number of good scores needed to advance

# Demotion criteria
DEMOTE_SCORE_THRESHOLD = 50   # Scores below this count as "poor"
DEMOTE_REQUIRED_POOR = 4      # Number of poor scores to trigger demotion

# Story generation
STORY_SENTENCE_COUNT = 30     # Number of sentences per story


class TongueRound:
    def __init__(self, sentence, difficulty, generate_ms):
        self.sentence = sentence
        self.difficulty = difficulty
        self.generate_ms = generate_ms
        self.judge_ms = None
        self.translation = None
        self.judgement = None
        self.evaluated = False

    def to_dict(self):
        return {
            'sentence': self.sentence,
            'difficulty': self.difficulty,
            'generate_ms': self.generate_ms,
            'judge_ms': self.judge_ms,
            'translation': self.translation,
            'judgement': self.judgement,
            'evaluated': self.evaluated
        }

    @classmethod
    def from_dict(cls, data):
        round = cls(data['sentence'], data['difficulty'], data['generate_ms'])
        round.judge_ms = data.get('judge_ms')
        round.translation = data.get('translation')
        round.judgement = data.get('judgement')
        round.evaluated = data.get('evaluated', False)
        return round

    def printRoundSummary(self):
        print('-' * 40)
        print(f'Input sentence in {LANGUAGE} (level {self.difficulty}/{MAX_DIFFICULTY}): {self.sentence}')
        print(f'Your translation: {self.translation}')
        print(f'Judgement time: {self.judge_ms}ms')
        correct_translation = self.judgement['correct_translation']
        print(f"Correct translation: {correct_translation}")
        score = int(self.judgement['score'])
        print(f"Score: {score}")
        if score < 100:
            evaluation = self.judgement['evaluation']
            print(f"Reason: {evaluation}")
        print('-' * 40)


def split_into_sentences(text):
    """Split a story into individual sentences."""
    # Handle Spanish punctuation (¿ ¡) and standard punctuation
    # Split on . ! ? but keep the punctuation with the sentence
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())

    # Clean up sentences
    cleaned = []
    for s in sentences:
        s = s.strip()
        # Remove any numbering like "1." or "1)" at the start
        s = re.sub(r'^\d+[.)]\s*', '', s)
        if s and len(s) > 3:  # Skip very short fragments
            cleaned.append(s)

    return cleaned


class History:
    def __init__(self):
        self.rounds = []
        self.correct_words = []
        self.missed_words = {}  # {word: {english: str, count: int}}
        self.difficulty = MIN_DIFFICULTY
        self.level_scores = []  # Recent scores at current difficulty
        self.total_completed = 0
        # Story-related fields
        self.current_story = None           # The full story text
        self.story_sentences = []           # Remaining sentences from current story
        self.story_difficulty = None        # Difficulty level the story was generated for
        self.story_generate_ms = 0          # Time taken to generate the story

    def to_dict(self):
        return {
            'rounds': [r.to_dict() for r in self.rounds],
            'correct_words': self.correct_words,
            'missed_words': self.missed_words,
            'difficulty': self.difficulty,
            'level_scores': self.level_scores,
            'total_completed': self.total_completed,
            'current_story': self.current_story,
            'story_sentences': self.story_sentences,
            'story_difficulty': self.story_difficulty,
            'story_generate_ms': self.story_generate_ms
        }

    @classmethod
    def from_dict(cls, data):
        history = cls()
        history.rounds = [TongueRound.from_dict(r) for r in data.get('rounds', [])]
        # Deduplicate correct_words while preserving order
        seen = set()
        correct_words = []
        for word in data.get('correct_words', []):
            if word not in seen:
                seen.add(word)
                correct_words.append(word)
        history.correct_words = correct_words
        history.missed_words = data.get('missed_words', {})
        history.difficulty = data.get('difficulty', MIN_DIFFICULTY)
        history.level_scores = data.get('level_scores', [])
        history.total_completed = data.get('total_completed', 0)
        # Story-related fields
        history.current_story = data.get('current_story')
        history.story_sentences = data.get('story_sentences', [])
        history.story_difficulty = data.get('story_difficulty')
        history.story_generate_ms = data.get('story_generate_ms', 0)
        return history

    def record_score(self, difficulty, score):
        """Record a score for advancement tracking."""
        # Only track scores at current difficulty
        if difficulty == self.difficulty:
            self.level_scores.append(score)
            # Keep only the last ADVANCE_WINDOW_SIZE scores
            if len(self.level_scores) > ADVANCE_WINDOW_SIZE:
                self.level_scores = self.level_scores[-ADVANCE_WINDOW_SIZE:]

    def get_good_score_count(self):
        """Count how many recent scores are >= threshold."""
        return sum(1 for s in self.level_scores if s >= ADVANCE_SCORE_THRESHOLD)

    def get_poor_score_count(self):
        """Count how many recent scores are < demotion threshold."""
        return sum(1 for s in self.level_scores if s < DEMOTE_SCORE_THRESHOLD)

    def check_advancement(self):
        """Check if player should advance to next level."""
        if self.difficulty >= MAX_DIFFICULTY:
            return False
        if len(self.level_scores) >= ADVANCE_WINDOW_SIZE:
            good_count = self.get_good_score_count()
            if good_count >= ADVANCE_REQUIRED_GOOD:
                return True
        return False

    def check_demotion(self):
        """Check if player should drop to previous level."""
        if self.difficulty <= MIN_DIFFICULTY:
            return False
        if len(self.level_scores) >= ADVANCE_WINDOW_SIZE:
            poor_count = self.get_poor_score_count()
            if poor_count >= DEMOTE_REQUIRED_POOR:
                return True
        return False

    def advance_level(self):
        """Advance to the next difficulty level."""
        if self.difficulty < MAX_DIFFICULTY:
            self.difficulty += 1
            self.level_scores = []  # Reset progress for new level
            # Clear story so new one generates at new level
            self.story_sentences = []
            self.current_story = None
            print('=' * 40)
            print(f'LEVEL UP! You are now at level {self.difficulty}/{MAX_DIFFICULTY}')
            print('A new story will be generated for this level.')
            print('=' * 40)
            return True
        return False

    def demote_level(self):
        """Drop to the previous difficulty level."""
        if self.difficulty > MIN_DIFFICULTY:
            self.difficulty -= 1
            self.level_scores = []  # Reset progress for new level
            # Clear story so new one generates at new level
            self.story_sentences = []
            self.current_story = None
            print('=' * 40)
            print(f'LEVEL DOWN. You are now at level {self.difficulty}/{MAX_DIFFICULTY}')
            print('Keep practicing! A new story will be generated for this level.')
            print('=' * 40)
            return True
        return False

    def get_progress_display(self):
        """Get a string showing current level and progress."""
        good_count = self.get_good_score_count()
        attempts = len(self.level_scores)
        if self.difficulty >= MAX_DIFFICULTY:
            return f"Level {self.difficulty}/{MAX_DIFFICULTY} (MAX)"
        return f"Level {self.difficulty}/{MAX_DIFFICULTY} | Progress: {good_count}/{ADVANCE_REQUIRED_GOOD} good scores (last {attempts}/{ADVANCE_WINDOW_SIZE})"

    def print_story_with_highlight(self, current_sentence):
        """Print the full story with the current sentence highlighted."""
        if not self.current_story:
            return

        print('\n' + '=' * 60)
        print(f'STORY (Level {self.story_difficulty})')
        print('=' * 60)

        # Get all sentences from the story
        all_sentences = split_into_sentences(self.current_story)

        for sentence in all_sentences:
            if sentence == current_sentence:
                # Highlight current sentence
                print(f'\n  >>> {sentence} <<<\n')
            else:
                print(f'  {sentence}')

        print('=' * 60)

    def evaluate_round(self, round, chat):
        """Evaluate a round synchronously and process results."""
        print('Validating translation...')
        round.judgement, round.judge_ms = validate_response(round.sentence, round.translation, chat)
        round.evaluated = True

        # Record score and update words
        score = int(round.judgement['score'])
        self.record_score(round.difficulty, score)
        self.updateCorrectWords(round)
        self.total_completed += 1

        # Print results
        round.printRoundSummary()

        # Check for level changes
        if self.check_advancement():
            self.advance_level()
        elif self.check_demotion():
            self.demote_level()

        # Print summary
        print('=' * 40)
        print(f'Total completed: {self.total_completed} | {self.get_progress_display()}')
        if self.level_scores:
            avg = sum(self.level_scores) / len(self.level_scores)
            poor_count = self.get_poor_score_count()
            print(f'Recent average: {avg:.1f} (need {ADVANCE_SCORE_THRESHOLD}+ for good, <{DEMOTE_SCORE_THRESHOLD} is poor)')
            if self.difficulty > MIN_DIFFICULTY:
                if poor_count >= 2:
                    print(f'Warning: {poor_count}/{DEMOTE_REQUIRED_POOR} poor scores - risk of demotion!')
            else:
                if poor_count >= 2:
                    print(f'Note: {poor_count} poor scores (already at minimum level)')
        print('=' * 40)

        save_state()

    def needs_new_story(self):
        """Check if we need to generate a new story."""
        # Need new story if no sentences left or difficulty changed
        if not self.story_sentences:
            return True
        if self.story_difficulty != self.difficulty:
            return True
        return False

    def generate_new_story(self, chat):
        """Generate a new story synchronously."""
        print(f"\nGenerating a new story at level {self.difficulty}...")
        correct_words = self.get_correct_words()
        story, ms = generate_story(chat, correct_words, self.difficulty)

        # Split story into sentences
        sentences = split_into_sentences(story)

        # Store in history
        self.current_story = story
        self.story_sentences = sentences
        self.story_difficulty = self.difficulty
        self.story_generate_ms = ms

        print(f"Story ready! ({len(self.story_sentences)} sentences, {ms}ms)")
        save_state()

    def get_next_sentence(self, chat):
        """Get the next sentence to translate from the current story."""
        # Generate new story if needed
        if self.needs_new_story():
            self.generate_new_story(chat)

        # Get the next sentence from the story
        sentence = self.story_sentences.pop(0)
        round = TongueRound(sentence, self.story_difficulty, self.story_generate_ms)
        self.rounds.append(round)

        return round

    def get_correct_words(self):
        return self.correct_words

    def updateCorrectWords(self, round):
        for v_breakdown in round.judgement['vocabulary_breakdown']:
            word = v_breakdown[0]
            english = v_breakdown[1]
            part_of_speech = v_breakdown[2].lower()
            was_correct = v_breakdown[3]

            if was_correct and part_of_speech in ['noun', 'verb']:
                if word not in self.correct_words:
                    self.correct_words.append(word)
                # Remove from missed words if mastered
                if word in self.missed_words:
                    del self.missed_words[word]
            elif not was_correct:
                # Track missed word
                if word in self.missed_words:
                    self.missed_words[word]['count'] += 1
                else:
                    self.missed_words[word] = {'english': english, 'count': 1}

    def print_status(self):
        """Print detailed status summary."""
        print('\n' + '=' * 50)
        print('STATUS SUMMARY')
        print('=' * 50)

        # Basic info
        print(f'\nLanguage: {LANGUAGE}')
        print(f'Current level: {self.difficulty}/{MAX_DIFFICULTY}')
        print(f'Total sentences completed: {self.total_completed}')

        # Story progress
        if self.story_sentences:
            remaining = len(self.story_sentences)
            total = STORY_SENTENCE_COUNT
            completed_in_story = total - remaining
            print(f'Current story: {completed_in_story}/{total} sentences (level {self.story_difficulty})')
        else:
            print('Current story: completed (new story will generate next)')

        # Level progress
        if self.level_scores:
            avg = sum(self.level_scores) / len(self.level_scores)
            good_count = self.get_good_score_count()
            poor_count = self.get_poor_score_count()
            print(f'\nLevel {self.difficulty} stats (last {len(self.level_scores)} attempts):')
            print(f'  Average score: {avg:.1f}')
            print(f'  Good scores (>={ADVANCE_SCORE_THRESHOLD}): {good_count}/{ADVANCE_REQUIRED_GOOD} needed to advance')
            print(f'  Poor scores (<{DEMOTE_SCORE_THRESHOLD}): {poor_count}/{DEMOTE_REQUIRED_POOR} triggers demotion')
        else:
            print(f'\nNo scores yet at level {self.difficulty}')

        # Mastered words
        print(f'\nMastered words: {len(self.correct_words)}')
        if self.correct_words:
            # Show last 10 mastered words
            recent = self.correct_words[-10:]
            print(f'  Recent: {", ".join(recent)}')

        # Missed words
        print(f'\nWords to practice: {len(self.missed_words)}')
        if self.missed_words:
            # Sort by count (most missed first)
            sorted_missed = sorted(self.missed_words.items(), key=lambda x: x[1]['count'], reverse=True)
            print('  Word | Translation | Times missed')
            print('  ' + '-' * 40)
            for word, info in sorted_missed[:15]:  # Show top 15
                print(f'  {word} | {info["english"]} | {info["count"]}')
            if len(sorted_missed) > 15:
                print(f'  ... and {len(sorted_missed) - 15} more')

        print('\n' + '=' * 50 + '\n')


history = History()


def save_state():
    """Save the current state to file."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(history.to_dict(), f, indent=2)
        if DEBUG:
            print(f"State saved to {STATE_FILE}")
    except Exception as e:
        print(f"Warning: Failed to save state: {e}")


def load_state():
    """Load state from file if it exists."""
    global history
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
            history = History.from_dict(data)
            print(f"Restored: {history.total_completed} completed, {history.get_progress_display()}")
            if history.story_sentences:
                print(f"Story in progress: {len(history.story_sentences)} sentences remaining at level {history.story_difficulty}")
            return True
        except Exception as e:
            print(f"Warning: Failed to load state: {e}")
            return False
    return False


def load_config():
    """Load configuration from config file."""
    if not os.path.exists(CONFIG_FILE):
        print(f"Error: Config file not found at {CONFIG_FILE}")
        print(f"Please create it with the following content:")
        print(f'{{"gemini_api_key": "YOUR_API_KEY_HERE"}}')
        raise SystemExit(1)

    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)


def initialize_gemini():
    config = load_config()
    api_key = config.get('gemini_api_key')
    if not api_key:
        print("Error: gemini_api_key not found in config file")
        raise SystemExit(1)
    genai.configure(api_key=api_key)
    print(f'Using model: {GEMINI_MODEL}')
    return genai.GenerativeModel(GEMINI_MODEL)


def execute_chat(chat, prompt):
    start_time = time.time()
    response = chat.send_message(prompt)
    end_time = time.time()
    ms = int((end_time - start_time) * 1000)
    if DEBUG:
        print(f"   Milliseconds taken: {ms}")
    return (response.text, ms)


def sanitize_judgement(judgement):
    s = judgement[judgement.find('{'):judgement.rfind('}')+1]
    s = s.replace('false', 'False')
    s = s.replace('true', 'True')
    return s


def generate_story(chat, correct_words, difficulty):
    """Generate a short story with approximately 30 sentences."""
    # Limit the avoided words list to prevent prompt from being too long
    avoided_words = correct_words[-100:] if len(correct_words) > 100 else correct_words

    prompt = f"""
        Write a short, engaging story in {LANGUAGE} with approximately {STORY_SENTENCE_COUNT} sentences.
        The story should be a random, creative narrative (adventure, mystery, daily life, fantasy, etc.).

        Requirements:
        - Difficulty level: {difficulty} out of {MAX_DIFFICULTY}
          Level 1 = beginner (simple vocabulary, present tense, basic grammar)
          Level 5 = intermediate (varied vocabulary, multiple tenses, compound sentences)
          Level 10 = expert (advanced vocabulary, subjunctive, idioms, complex grammar)
        - Each sentence should be 5-20 words long
        - Use vocabulary and grammar appropriate for level {difficulty}
        - Try to avoid using these nouns (the student already knows them): {', '.join(avoided_words) if avoided_words else 'none'}

        Write only the story text, no titles, no translations, no explanations.
        Each sentence should end with proper punctuation (. ! ?).
    """
    story, ms = execute_chat(chat, prompt)
    return (story, ms)


def get_hint(sentence, correct_words, chat):
    """Get a hint by translating one noun and one verb from the sentence."""
    prompt = f"""
        Analyze this {LANGUAGE} sentence and provide a hint for translation:

        Sentence: "{sentence}"

        Already known words (do NOT include these): {', '.join(correct_words[-50:]) if correct_words else 'none'}

        Find ONE noun and ONE verb from the sentence that are NOT in the known words list.
        Provide their English translations.

        Respond with ONLY a Python dictionary in this exact format:
        {{
            'noun': ['spanish_noun', 'english_translation'],
            'verb': ['spanish_verb', 'english_translation']
        }}

        If no noun or verb is available (all are known), use null for that entry:
        {{'noun': null, 'verb': ['spanish_verb', 'english_translation']}}

        Return ONLY the dictionary, no other text.
    """
    response, ms = execute_chat(chat, prompt)
    try:
        # Clean up the response
        response = response.strip()
        response = response.replace('null', 'None')
        response = response.replace('```python', '').replace('```', '')
        hint = eval(response)
        return hint
    except:
        return None


def validate_response(sentence, translation, chat):
    prompt_judge = f"""
        You are evaluating a student's English translation of a {LANGUAGE} sentence.

        {LANGUAGE} Sentence: "{sentence}"
        Student's Translation: "{translation}"

        STEP 1 - ANALYZE THE ORIGINAL SENTENCE FIRST:
        Before evaluating, carefully identify ALL words and their meanings in the {LANGUAGE} sentence.
        Consider all possible valid English translations for each word, including synonyms.

        STEP 2 - EVALUATION RULES:
        1. A word is CORRECTLY translated if the student used a valid English equivalent.
           Multiple English words can be valid translations (e.g., "vista" can be "view", "sight", "vision").
        2. A word is INCORRECTLY translated if:
           - Left in {LANGUAGE} (not translated at all, e.g., "casa" instead of "house")
           - Translated to a wrong meaning
           - Omitted entirely
        3. Do NOT penalize the student for:
           - Using synonyms (these are valid translations)
           - Minor word order differences that don't change meaning
           - Using articles (a/an/the) flexibly
        4. Only proper nouns (names of people, places, brands) may remain in original form.

        STEP 3 - RESPOND with a Python dictionary:

        'vocabulary_breakdown': FIRST, create this list analyzing the ORIGINAL {LANGUAGE} sentence.
        A list of lists, one for each meaningful word/phrase:
          [0] The {LANGUAGE} word/phrase from the original sentence
          [1] The correct English translation(s) for this word in context
          [2] Part of speech (noun, verb, adjective, etc.)
          [3] Boolean: True if the student correctly translated this word (including valid synonyms).
              False only if: untranslated, mistranslated, or omitted.

        'correct_translation': A proper English translation of the sentence.

        'score': Integer 1-100. Base it on:
          - What percentage of words were correctly translated
          - Deduct heavily for untranslated {LANGUAGE} words left as-is
          - Deduct for wrong meanings
          - Minor deductions for grammar issues

        'evaluation': Brief explanation. Be ACCURATE - do not claim a word was "added" if it's
        a valid translation of a word in the original sentence. Only mention actual errors.

        Return ONLY the dictionary, no other text, no markdown formatting.
    """
    judgement, ms = execute_chat(chat, prompt_judge)
    judgement = sanitize_judgement(judgement)
    judgement = eval(judgement)
    return (judgement, ms)


def main():
    global history

    model = initialize_gemini()
    chat = model.start_chat(history=[])

    # Load previous state if available
    load_state()

    print(f'\nStarting {LANGUAGE} translation practice!')
    print(f'Level up: Score {ADVANCE_SCORE_THRESHOLD}+ on {ADVANCE_REQUIRED_GOOD}/{ADVANCE_WINDOW_SIZE} recent attempts')
    print(f'Level down: Score <{DEMOTE_SCORE_THRESHOLD} on {DEMOTE_REQUIRED_POOR}/{ADVANCE_WINDOW_SIZE} recent attempts')
    print('Commands: "hint" for help, "status" for progress, "exit" to quit\n')

    # Main infinite loop
    while True:
        # Get next sentence
        round = history.get_next_sentence(chat)

        # Display story with current sentence highlighted
        history.print_story_with_highlight(round.sentence)

        # Display progress and prompt
        print(f"\n{history.get_progress_display()}")
        print(f"\n>>> {round.sentence}")

        translation = ''
        while not translation:
            user_input = input('==> ').strip()
            if user_input.lower() == 'exit':
                save_state()
                print('State saved. Goodbye!')
                return
            elif user_input.lower() == 'hint':
                print('Getting hint...')
                hint = get_hint(round.sentence, history.correct_words, chat)
                if hint:
                    print('\n--- HINT ---')
                    if hint.get('noun'):
                        print(f"  Noun: {hint['noun'][0]} = {hint['noun'][1]}")
                    if hint.get('verb'):
                        print(f"  Verb: {hint['verb'][0]} = {hint['verb'][1]}")
                    print('------------')
                else:
                    print('Could not generate hint.')
                print(f"\n>>> {round.sentence}")
            elif user_input.lower() == 'status':
                history.print_status()
                print(f"\n>>> {round.sentence}")
            elif user_input == '':
                history.print_story_with_highlight(round.sentence)
                print(f"\n>>> {round.sentence}")
            else:
                translation = user_input

        round.translation = translation
        save_state()

        # Evaluate synchronously
        history.evaluate_round(round, chat)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print('\n')
        save_state()
        print('State saved. Closing down.')
