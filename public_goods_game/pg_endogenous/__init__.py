from otree.api import *
import json




doc = """
T3/T4: Endogenous firms (live formation) + constant/increasing returns.
"""




class C(BaseConstants):
   NAME_IN_URL = 'pg_endogenous'
   PLAYERS_PER_GROUP = None
   NUM_ROUNDS = 30


   ENDOWMENT = 8
   FORMATION_SECONDS = 120
   DECISION_SECONDS = 60
   INFO_SECONDS = 30


   MAX_FIRM_SIZE = 6


   # Table 2 MPCR (constant returns), indexed by firm size n
   MPCR_BY_SIZE = {2: 0.65, 3: 0.55, 4: 0.49, 5: 0.45, 6: 0.42}




class Subsession(BaseSubsession):
   formation_state = models.LongStringField(initial='')
   formation_finalized = models.BooleanField(initial=False)




class Group(BaseGroup):
   total_effort = models.FloatField(initial=0)
   firm_size = models.IntegerField(initial=0)
   per_capita_effort = models.FloatField(initial=0)
   per_capita_payout = models.FloatField(initial=0)




class Player(BasePlayer):
   # --- Formation outcome (set after firm formation is finalized) ---
   # 0 means autarky (singleton group)
   firm_owner_id = models.IntegerField(initial=0)


   # 0 means not employed by anyone; if employed elsewhere, this is the owner's id_in_subsession
   employer_id = models.IntegerField(initial=0)


   is_autarkic = models.BooleanField(initial=True)


   # --- Decision (effort allocation) ---
   effort_to_firm = models.FloatField(min=0, max=C.ENDOWMENT, initial=0)


   # --- Resume / history stats (saved each round so we can display later) ---
   # store member ids like "2,1,3" (easy to export/display)
   firm_members = models.LongStringField(initial="")


   firm_size = models.IntegerField(initial=1)
   firm_per_capita_effort = models.FloatField(initial=0)
   firm_per_capita_payout = models.FloatField(initial=0)


   # termination marker (set on the PREVIOUS round row when rejected next round)
   was_terminated = models.BooleanField(initial=False)






# ---------------------------
# Formation state (JSON)
# ---------------------------


def _initial_state(n_players: int):
   owners = [str(i) for i in range(1, n_players + 1)]
   return dict(
       pending={o: [] for o in owners},      # owner -> [applicant ids]
       accepted={o: [] for o in owners},     # owner -> [employee ids]
       employer={str(i): None for i in range(1, n_players + 1)},  # person -> owner id (or None)
       rejections=[],
   )




def _get_state(subsession: Subsession):
   if not subsession.formation_state:
       state = _initial_state(len(subsession.get_players()))
       subsession.formation_state = json.dumps(state)
       return state
   return json.loads(subsession.formation_state)




def _set_state(subsession: Subsession, state):
   subsession.formation_state = json.dumps(state)




def _remove_from_all_pending(state, applicant_id: int):
   for owner_s, apps in state['pending'].items():
       if applicant_id in apps:
           apps.remove(applicant_id)




def _auto_reject_incoming_if_owner_becomes_inactive(state, owner_id: int):
   owner_s = str(owner_id)
   incoming = list(state['pending'][owner_s])
   if incoming:
       for a in incoming:
           state['rejections'].append(dict(applicant=a, owner=owner_id, reason='owner_became_inactive'))
       state['pending'][owner_s] = []


def _resumes_for_all(subsession: Subsession):
   out = {}
   for p in subsession.get_players():
       hist = []
       for pr in p.in_previous_rounds():
           hist.append(dict(
               round=pr.round_number,
               firm_owner_id=pr.firm_owner_id,
               firm_size=pr.firm_size,
               firm_members=pr.firm_members,
               per_capita_effort=pr.firm_per_capita_effort,
               per_capita_payout=pr.firm_per_capita_payout,
               was_terminated=pr.was_terminated,
           ))
       out[str(p.id_in_subsession)] = hist
   return out




def _build_payload(subsession: Subsession, state):
   players = subsession.get_players()
   n = len(players)


   # ✅ create payload dict FIRST
   payload = {}


   # ✅ add resume/history info for UI
   payload["resumes"] = _resumes_for_all(subsession)
   payload["all_ids"] = [p.id_in_subsession for p in players]


   # outgoing applications: for each applicant id -> list of owners they applied to
   outgoing = {str(i): [] for i in range(1, n + 1)}
   for owner_s, apps in state["pending"].items():
       for a in apps:
           outgoing[str(a)].append(int(owner_s))


   firms = []
   for owner in range(1, n + 1):
       owner_s = str(owner)


       active = state["employer"][owner_s] is None
       employees = state["accepted"][owner_s]  # list of ints
       members = [owner] + employees
       pending = state["pending"][owner_s]


       slots_left = C.MAX_FIRM_SIZE - len(members)


       firms.append(dict(
           owner=owner,
           active=active,
           members=members,
           pending=pending,
           slots_left=slots_left,
       ))


   payload["firms"] = firms
   payload["employer"] = state["employer"]
   payload["outgoing"] = outgoing
   return payload


# ---------------------------
# Session setup
# ---------------------------


def creating_session(subsession: Subsession):
   players = subsession.get_players()


   # real sessions: must be 18; test sessions can be smaller
   test_mode = subsession.session.config.get('test_mode', False)
   if (not test_mode) and len(players) != 18:
       raise Exception(f"T3/T4 require exactly 18 participants; currently {len(players)}")


   # one big group during formation
   subsession.set_group_matrix([players])


   subsession.formation_state = json.dumps(_initial_state(len(players)))
   subsession.formation_finalized = False




# ---------------------------
# Live formation
# ---------------------------






def live_formation(player: Player, data):
   subsession = player.subsession
   state = _get_state(subsession)
   players = subsession.get_players()
   n = len(players)


   pid = player.id_in_subsession
   msg_type = data.get('type')


   def deny(msg):
       # IMPORTANT: do NOT return key 0 together with other keys
       return {
           player.id_in_group: dict(
               alert=msg,
               state=_build_payload(subsession, state),
           )
       }


   if msg_type == 'ping':
       return {player.id_in_group: dict(state=_build_payload(subsession, state))}


   employer = state['employer']
   pending = state['pending']
   accepted = state['accepted']


   if msg_type == 'apply':
       owner = int(data.get('owner', 0))
       if owner <= 0 or owner > n:
           return deny("Invalid firm.")
       if owner == pid:
           return deny("You cannot apply to your own firm.")
       if employer[str(pid)] is not None:
           return deny("You are already employed; acceptance is binding.")
       if len(accepted[str(pid)]) > 0:
           return deny("You have hired someone, so you can no longer apply elsewhere.")
       if employer[str(owner)] is not None:
           return deny("That firm is inactive (owner is employed elsewhere).")
       if 1 + len(accepted[str(owner)]) >= C.MAX_FIRM_SIZE:
           return deny("That firm is full.")
       if pid in pending[str(owner)]:
           return deny("You already applied to that firm.")
       pending[str(owner)].append(pid)


   elif msg_type == 'withdraw':
       owner = int(data.get('owner', 0))
       if owner <= 0 or owner > n:
           return deny("Invalid firm.")
       if employer[str(pid)] is not None:
           return deny("You cannot withdraw after being accepted.")
       if pid not in pending[str(owner)]:
           return deny("No pending application to withdraw.")
       pending[str(owner)].remove(pid)


   elif msg_type == 'accept':
       owner = int(data.get('owner', 0))
       applicant = int(data.get('applicant', 0))


       if owner != pid:
           return deny("Only the firm owner can accept applicants to this firm.")
       if employer[str(owner)] is not None:
           return deny("Your firm is inactive because you are employed elsewhere.")
       if applicant not in pending[str(owner)]:
           return deny("That application is not pending.")
       if employer[str(applicant)] is not None:
           return deny("Applicant is already employed elsewhere.")
       if len(accepted[str(applicant)]) > 0:
           return deny("Applicant cannot join because they already hired someone.")
       if 1 + len(accepted[str(owner)]) >= C.MAX_FIRM_SIZE:
           return deny("Your firm is full.")


       pending[str(owner)].remove(applicant)
       accepted[str(owner)].append(applicant)
       employer[str(applicant)] = owner


       # binding acceptance cancels other applications
       _remove_from_all_pending(state, applicant)
       # owner becomes bound; cancel owner applications
       _remove_from_all_pending(state, owner)
       # applicant’s own firm becomes inactive; reject incoming apps
       _auto_reject_incoming_if_owner_becomes_inactive(state, applicant)


   elif msg_type == 'reject':
       owner = int(data.get('owner', 0))
       applicant = int(data.get('applicant', 0))


       if owner != pid:
           return deny("Only the firm owner can reject applicants to this firm.")
       if applicant not in pending[str(owner)]:
           return deny("That application is not pending.")


       pending[str(owner)].remove(applicant)


       # record rejection; we decide later in finalize_formation whether it counts as a "termination"
       state['rejections'].append(dict(applicant=applicant, owner=owner, reason='rejected'))


   else:
       return deny("Unknown action.")


   _set_state(subsession, state)
   return {0: dict(state=_build_payload(subsession, state))}




# ---------------------------
# Finalize + regroup
# ---------------------------


def finalize_formation(group: Group):
   subsession = group.subsession
   state = _get_state(subsession)
   players = subsession.get_players()
   n = len(players)


   players_by_id = {p.id_in_subsession: p for p in players}


   # ------------------------------------------------------------
   # 1) Auto-reject any remaining pending applications at the end
   # ------------------------------------------------------------
   for owner_s, apps in state['pending'].items():
       owner = int(owner_s)
       for a in list(apps):
           state['rejections'].append(dict(applicant=a, owner=owner, reason='auto_end'))
       state['pending'][owner_s] = []


   # ------------------------------------------------------------
   # 2) Default everyone to autarky (singleton) until proven otherwise
   # ------------------------------------------------------------
   for p in players:
       p.is_autarkic = True
       p.firm_owner_id = 0
       p.employer_id = 0
       # (current round termination flag should remain default False;
       #  we mark termination on the PREVIOUS round row when relevant)


   matrix = []
   assigned = set()


   # Track which owners actually "operate" this period (firm size >= 2)
   operating_owners = set()


   # ------------------------------------------------------------
   # 3) Create firms for "active owners" (owners NOT employed elsewhere)
   #    A firm only exists if the owner has >=1 accepted employee (size>=2)
   # ------------------------------------------------------------
   for owner in range(1, n + 1):
       owner_s = str(owner)


       # owner is inactive if THEY are employed by someone else
       if state['employer'][owner_s] is not None:
           continue


       employees = state['accepted'][owner_s]  # list[int]


       # if no employees, owner is autarky (singleton) per current design
       if not employees:
           continue


       operating_owners.add(owner)


       member_ids = [owner] + employees
       matrix.append([players_by_id[i] for i in member_ids])


       for pid in member_ids:
           assigned.add(pid)
           p = players_by_id[pid]
           p.is_autarkic = False
           p.firm_owner_id = owner


           # employer_id: employees point to owner; owner has employer_id=0
           if pid == owner:
               p.employer_id = 0
           else:
               p.employer_id = owner


   # ------------------------------------------------------------
   # 4) Everyone not assigned goes to autarky singleton group
   # ------------------------------------------------------------
   for pid, p in players_by_id.items():
       if pid not in assigned:
           matrix.append([p])
           p.is_autarkic = True
           p.firm_owner_id = 0
           p.employer_id = 0


   # Apply the grouping for this round
   subsession.set_group_matrix(matrix)


   # ------------------------------------------------------------
   # 5) TERMINATION: mark previous round if rejected by prior employer
   #    AND the employer continues operating this period
   # ------------------------------------------------------------
   if subsession.round_number > 1:
       seen_pairs = set()  # avoid double-marking (applicant, owner)


       for r in state.get('rejections', []):
           applicant = int(r.get('applicant', 0))
           owner = int(r.get('owner', 0))


           if applicant <= 0 or owner <= 0:
               continue
           if (applicant, owner) in seen_pairs:
               continue
           seen_pairs.add((applicant, owner))


           # Only count as "termination" if the owner is operating this period
           if owner not in operating_owners:
               continue


           # Check if applicant worked for this owner LAST period
           app_p_current = players_by_id.get(applicant)
           if not app_p_current:
               continue


           prev_p = app_p_current.in_round(subsession.round_number - 1)


           if (not prev_p.is_autarkic) and (prev_p.firm_owner_id == owner):
               prev_p.was_terminated = True


   # Save state (rejections list etc.)
   _set_state(subsession, state)


# ---------------------------
# Payoffs
# ---------------------------


def set_payoffs(group: Group):
   players = group.get_players()
   n = len(players)


   # --- group-level stats (for Results/Relay/export) ---
   group.firm_size = n


   member_ids = [p.id_in_subsession for p in players]
   members_str = ",".join(str(x) for x in member_ids)


   # -----------------
   # Autarky (singleton)
   # -----------------
   if n == 1:
       p = players[0]


       group.total_effort = 0
       group.per_capita_effort = 0
       group.per_capita_payout = 0


       # resume/history fields
       p.is_autarkic = True
       p.firm_owner_id = 0
       p.employer_id = 0
       p.firm_members = members_str
       p.firm_size = 1
       p.firm_per_capita_effort = 0
       p.firm_per_capita_payout = 0


       # Paper: autarky earns 8 points
       p.payoff = C.ENDOWMENT
       return


   # -----------------
   # Firm (size >= 2)
   # -----------------
   total_effort = sum(p.effort_to_firm for p in players)
   group.total_effort = total_effort


   per_capita_effort = total_effort / n
   group.per_capita_effort = per_capita_effort


   # IMPORTANT: make this strict so you can't accidentally run the wrong treatment
   returns_type = group.session.config['returns_type']


   if returns_type == 'constant':
       # MPCR by size (2..6)
       # Example: C.MPCR_BY_SIZE = {2:0.65, 3:0.55, 4:0.49, 5:0.45, 6:0.42}
       if n not in C.MPCR_BY_SIZE:
           raise Exception(f"No MPCR specified for firm size n={n}. Check C.MPCR_BY_SIZE.")
       alpha = C.MPCR_BY_SIZE[n]
       per_capita_payout = alpha * total_effort


   elif returns_type == 'increasing':
       # power production: output = a * E^b, shared equally
       a = float(group.session.config['a'])
       b = float(group.session.config['b'])
       output = a * (total_effort ** b) if total_effort > 0 else 0.0
       per_capita_payout = output / n


   else:
       raise Exception(f"Unknown returns_type: {returns_type}")


   group.per_capita_payout = per_capita_payout


   # --- save per-player resume/history fields + payoff ---
   for p in players:
       p.is_autarkic = False
       p.firm_members = members_str
       p.firm_size = n
       p.firm_per_capita_effort = per_capita_effort
       p.firm_per_capita_payout = per_capita_payout


       # payoff = endowment - effort + per-capita payout
       p.payoff = (C.ENDOWMENT - p.effort_to_firm) + per_capita_payout




# ---------------------------
# Pages
# ---------------------------


class Formation(Page):
   live_method = live_formation


   @staticmethod
   def get_timeout_seconds(player: Player):
       # let you shorten in test sessions
       return player.session.config.get('formation_seconds', C.FORMATION_SECONDS)


   @staticmethod
   def js_vars(player: Player):
       return dict(
       my_id=player.id_in_subsession,
       max_size=C.MAX_FIRM_SIZE,
       formation_seconds=player.session.config.get(
           'formation_seconds', C.FORMATION_SECONDS
       )
   )




   @staticmethod
   def before_next_page(player: Player, timeout_happened):
       # In test_mode, skip the WaitPage and finalize here (once).
       if not player.session.config.get('test_mode', False):
           return
       subsession = player.subsession
       if not subsession.formation_finalized:
           subsession.formation_finalized = True
           finalize_formation(player.group)




class FormationWaitPage(WaitPage):
   after_all_players_arrive = finalize_formation


   @staticmethod
   def is_displayed(player: Player):
       return not player.session.config.get('test_mode', False)




class FirmAssignment(Page):
   @staticmethod
   def vars_for_template(player: Player):
       return dict(
           is_autarkic=(len(player.group.get_players()) == 1),
           firm_owner_id=player.firm_owner_id,
           members=[p.id_in_subsession for p in player.group.get_players()],
       )




class Decision(Page):
   timeout_seconds = C.DECISION_SECONDS
   form_model = 'player'
   form_fields = ['effort_to_firm']
   timeout_submission = dict(effort_to_firm=0.0)


   @staticmethod
   def is_displayed(player: Player):
       return len(player.group.get_players()) > 1

   @staticmethod
   def before_next_page(player: Player, timeout_happened):
       player.effort_to_firm = round(float(player.effort_to_firm or 0.0), 2)

   @staticmethod
   def error_message(player: Player, values):
       x = values.get('effort_to_firm')
       if x is None:
           return
       if abs(x - round(x, 2)) > 1e-9:
           return "Please choose effort in increments of 0.01."


class ResultsWaitPage(WaitPage):
   after_all_players_arrive = set_payoffs




class Results(Page):
   @staticmethod
   def vars_for_template(player: Player):
       return dict(
           is_autarkic=(len(player.group.get_players()) == 1),
           firm_size=len(player.group.get_players()),
           members=[p.id_in_subsession for p in player.group.get_players()],
       )




class Relay(Page):
   timeout_seconds = C.INFO_SECONDS


   @staticmethod
   def vars_for_template(player: Player):
       rows = []
       for g in player.subsession.get_groups():
           players = g.get_players()

           # In endogenous formation, each member of a firm has firm_owner_id set to the
           # owner's id_in_subsession. Autarky (unmatched) players have firm_owner_id == 0.
           owner_ids = {p.firm_owner_id for p in players}

           if owner_ids == {0}:
               # Autarky group. Include the player id so multiple autarkies are distinguishable.
               owner_id = 0
               owner_label = f"Autarky (P{players[0].id_in_subsession})"
           else:
               owner_ids.discard(0)
               owner_id = sorted(owner_ids)[0] if owner_ids else 0
               owner_label = f"P{owner_id}" if owner_id else "Autarky"

           rows.append(dict(
               firm_owner_id=owner_id,
               firm_owner_label=owner_label,
               firm_size=len(players),
               per_capita_effort=g.per_capita_effort,
               per_capita_payout=g.per_capita_payout,
           ))
       rows.sort(key=lambda r: (r['firm_size'], r['firm_owner_id']))
       return dict(rows=rows)





page_sequence = [
   Formation,
   FormationWaitPage,
   FirmAssignment,
   Decision,
   ResultsWaitPage,
   Results,
   Relay,
]



