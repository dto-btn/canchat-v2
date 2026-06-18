<script>
	import { goto } from '$app/navigation';
	import { acceptTerms } from '$lib/apis/terms';

	let accepting = false;

	async function handleAccept() {
		accepting = true;
		try {
			await acceptTerms(localStorage.token);
			await goto('/');
		} catch (e) {
			console.error('Failed to accept terms:', e);
			accepting = false;
		}
	}
</script>

<div class="w-full h-screen max-h-[100dvh] overflow-y-auto bg-white dark:bg-gray-950">
	<div class="max-w-5xl mx-auto px-4 py-6 text-black dark:text-white">
		<div class="flex flex-col md:flex-row items-start md:items-center justify-between">
			<h1 class="text-3xl font-bold mb-2 md:mb-0 md:mr-4 dark:text-white">
				CANChat – Conditions d'utilisation
			</h1>
			<a
				href="/terms"
				class="px-4 py-2 bg-purple-800 text-white rounded-md hover:bg-purple-800/80 transition-colors"
			>
				English
			</a>
		</div>

		<p class="text-sm text-neutral-600 dark:text-neutral-400 mt-1 pl-1">
			Date d'entrée en vigueur : 9 janvier 2026, Version : 3.0
		</p>

		<div
			class="mt-2 p-3 w-full rounded-lg border border-neutral-500 text-neutral-900 dark:text-neutral-100 dark:bg-gray-900 shadow [&_p]:ml-2"
		>
			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 pl-2 text-purple-700 dark:text-purple-400">
					Bienvenue à CANChat
				</h2>
				<p class="mb-3">
					CANChat est une solution d'intelligence artificielle générative avancée fournie par
					Services partagés Canada (SPC). Il est conçu pour aider le personnel du gouvernement du
					Canada (GC) à accroître la productivité, à rationaliser les tâches et à améliorer la
					communication.
				</p>
				<p class="mb-3">
					En accédant à CANChat, en l'utilisant ou en interagissant avec lui, vous, l'utilisateur,
					reconnaissez avoir lu et compris les présentes conditions d'utilisation, ainsi que toutes
					les politiques, directives et lois applicables du gouvernement du Canada, et acceptez
					d'être lié par celles-ci. <b
						>Si vous n'êtes pas d'accord avec ces termes, vous ne devez pas accéder à CANChat ou
						l'utiliser.</b
					>
				</p>
				<p class="mb-3">
					<b>Avertissement</b> : CANChat a été utilisé dans l'élaboration des Conditions d'utilisation.
					Tous les renseignements générés par l'IA ont été examinés par des analystes humains.
				</p>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					1. Définitions
				</h2>
				<ul class="list-disc pl-6">
					<li class="mb-2">
						<b>CANChat</b> : Désigne la solution d'intelligence artificielle générative, y compris ses
						modèles sous-jacents, son interface utilisateur, ses fonctions et les services connexes.
					</li>
					<li class="mb-2">
						<b>Utilisateur</b> : Toute personne autorisée à accéder à CANChat et à l'utiliser.
					</li>
					<li class="mb-2">
						<b>Données d'entrée</b> : Tout texte, document, fichier ou information qu'un utilisateur fournit
						ou télécharge dans CANChat.
					</li>
					<li class="mb-2">
						<b>Contenu de sortie</b> : Tout texte, code, image, résumé, traduction, analyse ou autre information
						générée ou fournie par CANChat en réponse aux données d'entrée de l'utilisateur.
					</li>
					<li class="mb-2">
						<b>Renseignements personnels (RP)</b> : Renseignements sur un individu identifiable qui
						sont consignés sous quelque forme que ce soit. Voir l'article 3 de la
						<a
							class="underline"
							target="_blank"
							rel="noopener noreferrer"
							href="https://laws-lois.justice.gc.ca/fra/lois/p-21/TexteComplet.html"
							>Loi sur la protection des renseignements personnels</a
						> pour obtenir des renseignements supplémentaires.
					</li>
					<li class="mb-2">
						<b>Protégé A</b> : S'applique aux renseignements on pourrait vraisemblablement s'attendre
						à ce que la divulgation non autorisée cause un préjudice limité ou modéré à des intérêts autres
						que l'intérêt national, par exemple, la divulgation d'un salaire exact.
					</li>
					<li class="mb-2">
						<b>Protégé B</b> : S'applique aux renseignements dont on pourrait vraisemblablement s'attendre
						à ce que la divulgation non autorisée cause un préjudice sérieux à des intérêts autres que
						l'intérêt national, par exemple, la perte de réputation ou d'avantage concurrentiel.
					</li>
					<li class="mb-2">
						<b>Protégé C</b> : S'applique à un nombre très restreint de renseignements dont on pourrait
						raisonnablement s'attendre à ce que la divulgation non autorisée cause un préjudice extrêmement
						grave à des intérêts autres que l'intérêt national, par exemple, la perte de vie.
					</li>
					<li class="mb-2">
						<b>Information ou bien de nature délicate</b> : Tout renseignement ou bien dont la compromission
						risquerait vraisemblablement de causer un préjudice. Cela comprend tous les renseignements
						qui répondent aux critères d'exemption ou d'exclusion de la Loi sur l'accès à l'information
						et de la Loi sur la protection des renseignements personnels. Cela comprend également les
						marchandises contrôlées ainsi que d'autres renseignements et biens qui font l'objet d'interdictions
						et de contrôles réglementaires ou législatifs.
					</li>
					<li class="mb-2">
						<b>Agrégation</b> : L'agrégation désigne un groupe de ressources ou de biens
						d'information. La catégorie de sécurité pour les dépôts d'information ou de biens
						reflète l'incidence de l'agrégation, où un préjudice plus important peut survenir
						lorsqu'un groupe de ressources documentaires ou de biens est compromis. En tant
						qu'utilisateur de CANChat, il incombe aux utilisateurs d'utiliser les données de façon
						appropriée conformément au
						<a
							class="underline"
							target="_blank"
							rel="noopener noreferrer"
							href="https://www.canada.ca/fr/gouvernement/systeme/gouvernement-numerique/guide-pratiques-relatives-privee-numerique/comment-utiliser-partager.html"
							>Guide sur les pratiques relatives à la vie privée numérique – Utilisation et partage
							des renseignements personnels</a
						>. Cela comprend l'agrégation et la dépersonnalisation des données dans la mesure du
						possible.
					</li>
					<li class="mb-2">
						<b>Fournisseurs tiers</b> : Des entreprises externes (par exemple, le service OpenAI de Microsoft,
						Meta, Google, Cohere, etc.) qui fournissent les grands modèles de langage (LLM) utilisés par
						CANChat.
					</li>
					<li class="mb-2">
						<b>Document éphémère</b> : Les documents éphémères n'ont aucune valeur opérationnelle.
						Ils peuvent comprendre des documents qui servent uniquement de copies de commodité de
						documents conservés dans un dépôt d'une institution fédérale, mais ne comprennent pas
						les documents nécessaires pour contrôler, appuyer ou documenter la prestation de
						programmes, pour exécuter des opérations, prendre des décisions ou fournir des preuves
						pour rendre compte des activités du gouvernement en tout temps. (Source :
						<a
							class="underline"
							target="_blank"
							rel="noopener noreferrer"
							href="https://www.canada.ca/fr/bibliotheque-archives/services/gouvernement/information-disposition/documents/autorisations-disposition-pluri-institutionnelles/2016-001-ad-documents-ephemeres.html"
							>Autorisation de disposition no 2016/001 pour les documents éphémères, section A.2</a
						>).
					</li>
				</ul>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					2. À propos de CANChat
				</h2>
				<p class="mb-2">
					CANChat est un outil précieux qui favorise l'exécution efficace des tâches. Ses capacités
					comprennent l'élaboration de communications percutantes, le relèvement des défis en
					matière de traduction, le raffinement des réponses par courriel, la génération de
					sommaires de documents, la réalisation de recherches préliminaires et l'accomplissement
					d'une multitude d'autres tâches.
				</p>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					3. Composantes
				</h2>
				<ul class="list-disc pl-6">
					<li class="mb-2">
						CANChat tire parti de grands modèles de langage de fournisseurs tiers, notamment OpenAI,
						Meta, Google, Cohere et d'autres.
					</li>
					<li class="mb-2">
						CANChat utilise une interface utilisateur libre (OpenWebUI) sous <a
							class="underline"
							target="_blank"
							rel="noopener noreferrer"
							href="https://mit-license.org/">licence MIT</a
						>.
					</li>
				</ul>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					4. Caractéristiques
				</h2>
				<p class="mb-3">
					L'interface utilisateur NextGen de CANChat introduit un éventail de fonctions et de
					caractéristiques innovantes, invitant les utilisateurs à découvrir et à intégrer ces
					améliorations et bien plus encore dans leur expérience:
				</p>
				<ul class="list-disc pl-6">
					<li class="mb-2">
						<b>Apportez vos propres données</b> : Utilisez la fonction de téléversement de CANChat pour
						travailler avec vos propres documents.
					</li>
					<li class="mb-2">
						<b>Historique des chats</b> : Passez en revue les chats antérieures avec CANChat pour reprendre
						les discussions ou revoir l'information.
					</li>
					<li class="mb-2">
						<b>Organisations des chats par étiquettes</b> : Ajoutez des étiquettes à vos chats pour qu'ils
						soient faciles à trier et à retrouver.
					</li>
					<li class="mb-2">
						<b>Sélection de la langue</b> : Communiquez avec CANChat dans la langue de votre choix.
					</li>
					<li class="mb-2">
						<b>Accessibilité mobile</b> : Connectez-vous à CANChat en déplacement avec une interface compatible
						avec les appareils mobiles.
					</li>
					<li class="mb-2">
						<b>Sélection de modèles</b> : Choisissez parmi une sélection de modèles d'IA dans CANChat
						pour mieux correspondre à la tâche à accomplir.
					</li>
					<li class="mb-2">
						<b>Soutien multi-modèles</b> : Utilisez plusieurs modèles d'IA dans une seule session pour
						comparer les résultats et améliorer vos réponses.
					</li>
					<li class="mb-2">
						<b>Personnalisation</b> : Personnalisez votre expérience CANChat avec des paramètres personnalisables
						selon vos préférences.
					</li>
					<li class="mb-2">
						<b>Recherches Web</b> : Utilisez les capacités de recherche Web de CANChat pour extraire rapidement
						de l'information des sites Web.
					</li>
				</ul>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-4 text-purple-700 dark:text-purple-400">
					5. Politique sur l'utilisation acceptable
				</h2>
				<p class="mb-4">
					Lorsque vous utilisez CANChat à des fins professionnelles, vous devez respecter
					rigoureusement toutes les politiques, normes et directives applicables du gouvernement du
					Canada et du Ministère sur l'utilisation responsable de l'IA, les services numériques, la
					gestion de l'information et la sécurité. Ces conditions d'utilisation s'harmonisent avec
					les politiques, les cadres et les lignes directrices clés du gouvernement du Canada, y
					compris le
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=17151"
						>Cadre stratégique sur la gestion de la conformité</a
					>, la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://laws-lois.justice.gc.ca/fra/lois/p-21/TexteComplet.html"
						>Loi sur la protection des renseignements personnels</a
					>, la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=32603"
						>Politique sur les services et le numérique</a
					>, la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=32601"
						>Directive sur les services et le numérique</a
					>, la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=16578&section=html"
						>Politique sur la sécurité du gouvernement</a
					>, la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=32592"
						>Directive sur la prise de décisions automatisée</a
					>, la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://laws-lois.justice.gc.ca/fra/lois/p-32/TexteComplet.html"
						>Loi sur les inventions des fonctionnaires</a
					>, la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=13697"
						>Politique sur le titre de propriété intellectuelle découlant des marchés d'acquisition
						de l'État</a
					>, la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=32611"
						>Directive sur la gestion de la sécurité</a
					>, le
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://gcxgce.sharepoint.com/teams/1000538/Shared%20Documents/General/AI%20Governance/Guide%20de%20SPC%20sur%20l%E2%80%99utilisation%20de%20l%E2%80%99IA%20g%C3%A9n%C3%A9rative.pdf?web=1"
						>Guide de SPC sur l'utilisation de l'IA générative</a
					>, et le
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.canada.ca/fr/gouvernement/systeme/gouvernement-numerique/innovations-gouvernementales-numeriques/utilisation-responsable-ai/guide-utilisation-intelligence-artificielle-generative.html"
						>Guide sur l'utilisation de l'intelligence artificielle générative</a
					>.
				</p>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					5.1 Activités autorisées (pour les affaires officielles du GC seulement)
				</h2>
				<ul class="list-disc pl-6">
					<li class="mb-2">
						<b>Synthèse de documents</b> : Rapidement divers documents, articles, politiques, documents
						de recherche, rapports et notes de réunion, entre autres.
					</li>
					<li class="mb-2">
						<b>Traduction de texte</b> : Aider les employés à traduire divers documents, présentations,
						rapports et courriels.
					</li>
					<li class="mb-2">
						<b>Création de contenu</b> : Aider les employés à être plus créatifs dans la rédaction ou
						la communication en suggérant du texte qui pourrait être inclus dans des rapports, des blogues,
						des communiqués ou des présentations.
					</li>
					<li class="mb-2">
						<b>Révision du contenu</b> : Modifiez le ton, changez la formalité ou réduisez la longueur
						du contenu pour une communication plus efficace.
					</li>
					<li class="mb-2">
						<b>Recherche et analyse</b> : Effectuer des recherches et des analyses. Cette capacité peut
						faire gagner beaucoup de temps précieux aux chercheurs et aux analystes, surtout en cas de
						demandes urgentes ou de grandes quantités de documents.
					</li>
					<li class="mb-2">
						<b>Préparation des réunions</b> : Produire des ordres du jour, des points de discussion et
						des résumés à partir des transcriptions et faciliter la préparation et le suivi efficaces
						des réunions.
					</li>
					<li class="mb-2">
						<b>Aide avec Microsoft Office</b> : Aide à générer des formules Excel, des graphiques et d'autres
						fonctionnalités de l'outil Office pour simplifier la gestion et l'analyse des données.
					</li>
				</ul>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					5.2 Activités interdites
				</h2>
				<p class="mb-2">Vous ne devez pas et ne devez pas permettre à un tiers :</p>
				<ul class="list-disc pl-6">
					<li class="mb-2">
						<b>Entrer des données au-delà du niveau Protégé B</b> : Ne pas utiliser de renseignements
						ministériels dont la classification est supérieure à Protégé B (p. ex., Protégé C, Classifié,
						Secret, Très secret).
					</li>
					<li class="mb-2">
						<b>Renseignements sensibles interdits</b> : CANChat peut traiter des renseignements de niveau
						Protégé B. Toutefois, il incombe entièrement aux utilisateurs de s'assurer que les renseignements
						personnels ou sensibles soumis sont strictement essentiels à l'exercice de leurs fonctions
						et qu'ils respectent toutes les politiques pertinentes du gouvernement du Canada ainsi que
						les politiques, normes et directives de leur ministère en matière de sécurité et à la protection
						des renseignements personnels.
					</li>
					<li class="mb-2">
						<b>Production de codes pour le développement ou la production</b> :
						<ul class="list-disc pl-6 mt-2">
							<li class="mb-2">
								<b>Pour les employés de Services partagés Canada</b> : L'utilisation de CANChat ou de
								tout autre outil d'intelligence artificielle générative pour générer, élaborer ou intégrer
								des codes dans un environnement de développement ou de production est strictement interdite
								en raison de préoccupations liées à la propriété intellectuelle, au droit d'auteur et
								à la vulnérabilité en matière de sécurité.
							</li>
							<li class="mb-2">
								<b>Pour les employés d'autres ministères du gouvernement du Canada</b> : Avant d'utiliser
								CANChat pour la génération, le développement ou l'intégration de code, les utilisateurs
								sont fortement encouragés à consulter et à respecter les politiques, normes et directives
								spécifiques de leur ministère concernant l'utilisation responsable de l'IA générative
								pour le codage. Un examen indépendant de la sécurité et une autorisation en matière de
								propriété intellectuelle sont également fortement recommandés pour tout code destiné à
								des environnements de développement ou de production.
							</li>
						</ul>
					</li>
					<li class="mb-2">
						<b>Se livrer à des pratiques illégales ou contraires à l'éthique</b> : N'utilisez pas
						CANChat à des fins illégales, contraires à l'éthique ou non autorisées, ou de quelque
						façon que ce soit qui contrevient aux lois ou aux règlements applicables. Veuillez
						consulter le
						<a
							class="underline"
							target="_blank"
							rel="noopener noreferrer"
							href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=25049"
							>Code de valeurs et d'éthique du secteur public</a
						>.
					</li>
					<li class="mb-2">
						<b>Générer du contenu préjudiciable</b> : Ne pas générer de contenu nuisible, offensant, malveillant,
						discriminatoire, haineux ou inapproprié.
					</li>
					<li class="mb-2">
						<b>Contourner les mesures de sécurité</b> : N'essayez pas de contourner les mesures de sécurité
						ministérielles, ne tentez pas d'exploiter les vulnérabilités dans CANChat ou les systèmes
						connexes, ni de vous livrer à toute forme d'accès non autorisé.
					</li>
					<li class="mb-2">
						<b>Usurper une identité</b> : N'usurpez pas l'identité d'une personne ou d'une entité, ne
						déclarez pas faussement ou ne présentez pas faussement votre affiliation à une personne ou
						à une entité.
					</li>
					<li class="mb-2">
						<b>Prise de décisions automatisée</b> : N'utilisez pas CANChat pour la prise de décisions
						automatisées qui touchent directement des personnes sans un examen humain complet, une validation
						et le respect de la Directive sur la prise de décisions automatisée.
					</li>
					<li class="mb-2">
						<b>Utilisation commerciale</b> : N'utilisez pas CANChat à des fins commerciales qui ne sont
						pas liées aux activités officielles du gouvernement du Canada.
					</li>
				</ul>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					6. Responsabilités des employés et limites de l'IA
				</h2>
				<p class="mb-3">
					Les employés doivent comprendre et connaître certaines limites des services d'IA
					génératifs. Lorsqu'on utilise des services comme ChatGPT pour rédiger des documents ou des
					réponses qui serviront à informer les décideurs, il est important de valider l'exactitude,
					la clarté, l'exhaustivité et la pertinence des extrants générés par ces services. Par
					conséquent, lorsqu'ils sont utilisés à titre officiel, les employés doivent:
				</p>
				<ul class="list-disc pl-6">
					<li class="mb-2">
						<b>Sensibilisation à l'actualité des données</b> : Reconnaître que l'efficacité et l'actualité
						des modèles d'IA sont influencées par l'actualité et l'âge de leurs données sur la formation.
						Le contenu de sortie peut ne pas refléter l'information ou les politiques les plus récentes.
					</li>
					<li class="mb-2">
						<b>Considérations éthiques et partialité</b> : Soyez conscient des préoccupations éthiques
						potentielles, y compris la possibilité de biais ou d'inexactitudes inhérents au contenu généré
						par l'IA en raison des biais dans les données de formation. Veiller à ce que son application
						soit conforme aux valeurs et aux objectifs de l'organisation, et travailler activement à cerner
						et à atténuer ces préjugés.
					</li>
					<li class="mb-2">
						<b>Supervision humaine et vérification</b> : Les employés doivent agir en tant qu'experts
						en la matière et assumer l'entière responsabilité de la vérification de l'exactitude, de l'exhaustivité
						et de la pertinence de tout contenu produit avant de s'y fier ou de le diffuser.
					</li>
					<li class="mb-2">
						<b>Conformité juridique et réglementaire</b> : Connaître et respecter toutes les exigences
						légales, les réglementations en matière de protection des renseignements personnels (Loi sur
						la protection des renseignements personnels) et les implications en matière de propriété intellectuelle
						lors de l'utilisation du contenu d'entrée ou de sortie de CANChat, en particulier lorsqu'il
						s'agit de traiter des informations confidentielles ou des informations Protégé B.
					</li>
					<li class="mb-2">
						<b>Divulgation responsable</b> : Indiquez clairement quand le contenu est généré avec l'aide
						de CANChat. Cela aide à maintenir la responsabilisation, facilite la compréhension des résultats
						générés par l'IA par les intervenants, favorise la confiance et atténue les risques potentiels
						(p. ex., en ajoutant un avertissement comme « Ce contenu a été généré avec l'aide de CANChat
						et validé par un humain »).
					</li>
					<li class="mb-2">
						<b>Atténuation des risques</b> : Comprendre les risques potentiels associés à l'IA générative,
						comme la création de contenu trompeur, l'amplification des biais ou la production d'hallucinations
						(renseignements plausibles mais inexacts).
					</li>
				</ul>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					7. Accord d'employé (acceptation des conditions)
				</h2>
				<p class="mb-3">En utilisant CANChat, vous êtes explicitement d'accord et confirmez que:</p>
				<ul class="list-disc pl-6">
					<li class="mb-2">
						Vous validerez tout le contenu généré par CANChat par un expert en la matière avant de
						l'inclure dans un milieu professionnel ou de vous y fier.
					</li>
					<li class="mb-2">
						Vous ne soumettrez pas de données d'entrée ou de renseignements dont le niveau de
						sensibilité est supérieur à Protégé B.
					</li>
					<li class="mb-2">
						Vous comprenez et acceptez les responsabilités associées au traitement de l'information
						« Protégé B » dans CANChat.
					</li>
					<li class="mb-2">
						Pour les employés de Services partagés Canada : Vous n'utiliserez pas CANChat pour
						générer, élaborer ou intégrer des codes dans un environnement de développement ou de
						production.
					</li>
					<li class="mb-2">
						Pour les employés d'autres ministères du gouvernement du Canada : Vous consulterez et
						respecterez vos politiques ministérielles concernant l'utilisation de l'IA générative
						pour la génération de codes.
					</li>
					<li class="mb-2">
						Vous ne vous livrerez pas à des pratiques illégales ou contraires à l'éthique.
					</li>
					<li class="mb-2">Vous ne générerez pas de contenu nuisible, offensant ou malveillant.</li>
					<li class="mb-2">
						Vous ne tenterez pas de contourner les mesures de sécurité ministérielles ou d'exploiter
						des vulnérabilités.
					</li>
					<li class="mb-2">
						Vous comprenez que CANChat est un service surveillé et que tous les interactions,
						données d'entrée et contenus de sortie peuvent être consignés et vérifiés.
					</li>
					<li class="mb-2">
						Vous comprenez que le soutien pour CANChat est fourni dans la mesure du possible par le
						programme d'intelligence artificielle, généralement offert pendant les heures normales
						de travail <b>(7 h 30 à 20 h, heure de l'est, du lundi au vendredi)</b>, et que le
						produit peut être mis hors service ou modifié sans préavis.
					</li>
				</ul>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					8. Droits de propriété intellectuelle (PI)
				</h2>
				<div class="mb-4">
					<h3 class="text-lg font-semibold mb-2 text-pink-500 dark:text-pink-400">
						8.1 Données d'entrée
					</h3>
					<ul class="list-disc pl-6">
						<li class="mb-2">
							En aucun cas vos données d'entrée ne seront utilisées pour la formation ou
							l'amélioration des modèles génératifs sous-jacents d'IA, que ce soit par SSC ou par
							des fournisseurs tiers.
						</li>
						<li class="mb-2">
							Vous déclarez et garantissez que vous avez tous les droits et autorisations
							nécessaires pour fournir des données d'entrée à CANChat et que vos données d'entrée ne
							portent pas atteinte aux droits de propriété intellectuelle, au droit à la vie privée
							ou à d'autres droits d'un tiers.
						</li>
					</ul>
				</div>
				<div>
					<h3 class="text-lg font-semibold mb-2 text-pink-500 dark:text-pink-400">
						8.2 Contenu de sortie
					</h3>
					<ul class="list-disc pl-6">
						<li class="mb-2">
							Pour le contenu de sortie généré par CANChat qui est créé par un fonctionnaire dans
							l'exercice de ses fonctions officielles à titre d'employé de la Couronne, ce contenu
							de sortie, une fois validé et adopté par le fonctionnaire dans le cadre de son
							travail, peut devenir la propriété de la Couronne. Les fonctionnaires doivent
							consulter la <a
								class="underline"
								target="_blank"
								rel="noopener noreferrer"
								href="https://laws-lois.justice.gc.ca/fra/lois/p-32/TexteComplet.html"
								>Loi sur les inventions des fonctionnaires</a
							> et vérifier auprès de leur ministère s'il existe des politiques liées à la mise en œuvre
							de ses dispositions.
						</li>
						<li class="mb-2">
							Les utilisateurs qui ne font pas partie de la fonction publique fédérale doivent
							consulter la <a
								class="underline"
								target="_blank"
								rel="noopener noreferrer"
								href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=13697"
								>Politique sur le titre de propriété intellectuelle découlant des marchés
								d'acquisition de l'État</a
							>.
						</li>
						<li class="mb-2">
							Vous reconnaissez que les modèles de CANChat sont formés sur de vastes ensembles de
							données, et bien que des efforts soient déployés pour respecter la PI, SPC ne peut
							garantir que le contenu de sortie sera exempt d'éléments qui pourraient empiéter sur
							les droits de propriété intellectuelle de tiers. Il incombe aux utilisateurs de faire
							preuve de diligence raisonnable et d'effectuer leur propre autorisation de PI pour le
							contenu de sortie, en particulier pour les communications externes ou les documents
							publiés.
						</li>
						<li class="mb-2">
							SPC ne fait aucune déclaration ou n'offre aucune garantie quant à l'originalité ou à
							l'absence de contrefaçon du contenu des extrants.
						</li>
					</ul>
				</div>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					9. Confidentialité et traitement des données
				</h2>
				<ul class="list-disc pl-6">
					<li class="mb-2">
						<b>Collecte des données</b> : SPC recueille et stocke les données d'entrée, le contenu de
						sortie, les journaux d'interaction, les mesures d'utilisation et l'information technique pour
						exploiter, surveiller, sécuriser et améliorer le système CANChat.
					</li>
					<li class="mb-2">
						<b>But de la collecte</b> :
						<ul class="list-disc pl-6 mt-2">
							<li class="mb-2">Fournir et maintenir le service CANChat.</li>
							<li class="mb-2">
								Surveiller la conformité aux présentes conditions d'utilisation et aux politiques
								pertinentes du gouvernement du Canada.
							</li>
							<li class="mb-2">Assurer la sécurité et l'intégrité du système.</li>
							<li class="mb-2">
								Déboguer et améliorer le rendement et la fonctionnalité de CANChat.
							</li>
							<li class="mb-2">
								Produire des rapports statistiques anonymisés et agrégés pour la gestion des
								services.
							</li>
							<li class="mb-2">
								Ce qui est crucial, c'est que les données d'entrée et le contenu de sortie ne seront
								pas utilisés pour la formation des modèles génératifs sous-jacents de l'IA.
							</li>
						</ul>
					</li>
					<li class="mb-2">
						<b>Partage avec des fournisseurs tiers</b> : En utilisant CANChat avec des données
						Protégé B, vous comprenez que vos données d'entrée et vos interactions avec CANChat
						seront traitées par les fournisseurs tiers (p. ex., Azure OpenAI Service, Cohere, Meta,
						Google) que vous sélectionnez au moyen de CANChat. Ce traitement est assujetti aux
						ententes du SSC avec ces fournisseurs, qui
						<b
							>stipulent explicitement que les données des utilisateurs ne seront pas utilisées à
							des fins de surveillance ou de formation</b
						>.
					</li>
					<li class="mb-2">
						<b>Stockage des données</b> : Les données des utilisateurs (données saisies, contenu produit,
						journaux) seront stockées sur une infrastructure approuvée par le gouvernement du Canada et
						seront traitées conformément à la classification de sécurité des données.
					</li>
					<li class="mb-2">
						<b>Conservation des données</b> : Toutes les données de chat (données d'entrée, contenu
						de sortie, registres d'interaction pour chaque chat)
						<b>sont conservées pendant 30 jours à partir de la dernière activité de chaque chat</b>.
						D'autres mesures d'utilisation et des rapports statistiques agrégés peuvent être
						conservés pour de plus longues périodes, conformément aux politiques et aux calendriers
						de gestion de l'information de SPC.
					</li>
					<li class="mb-2">
						<b>Renseignements personnels</b> : Services partagés Canada s'engage à protéger les
						renseignements personnels conformément à la Loi sur la protection des renseignements
						personnels. Lorsque les données Protégé B contenant des renseignements personnels sont
						traitées, tous les aspects de leur traitement seront conformes à la Loi sur la
						protection des renseignements personnels et à la
						<a
							class="underline"
							target="_blank"
							rel="noopener noreferrer"
							href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=18309"
							>Directive sur les pratiques relatives à la protection de la vie privée</a
						>.
					</li>
					<li class="mb-2">
						<b>Surveillance</b> : Vous reconnaissez que toute utilisation de CANChat, y compris les données
						d'entrée et le contenu de sortie, fait l'objet d'une surveillance et d'une vérification à
						des fins de sécurité, de conformité et d'enquête. Il n'y a aucune attente en matière de vie
						privée concernant votre utilisation de CANChat.
					</li>
					<li class="mb-2">
						<b>Continuité des activités</b> : L'approche de la continuité des activités de SPC met l'accent
						sur (1) le rétablissement opérationnel, qui rétablit la disponibilité du service sans récupérer
						les données des utilisateurs; et (2) le rétablissement complet, qui restaure à la fois le
						service et les données. Les utilisateurs de CANChat doivent savoir que la reprise opérationnelle
						sera priorisée afin de rétablir rapidement les fonctions de base. Les documents éphémères
						(p. ex., sessions transitoires, caches, index temporaires) seront récupérés dans la mesure
						du possible.
					</li>
				</ul>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					10. Exonération de garantie
				</h2>
				<p class="mb-2 font-semibold">
					LES UTILISATEURS RECONNAISSENT QUE CANCHAT N'A PAS ENCORE SUBI UN PROCESSUS D'AUTORISATION
					DES SERVICES COMPLET. PAR CONSÉQUENT, TOUT SOUTIEN FOURNI POUR CANCHAT EST OFFERT DANS LA
					MESURE DU POSSIBLE ET GÉNÉRALEMENT SEULEMENT PENDANT LES HEURES NORMALES D'OUVERTURE (7 h
					30 à 20 h, HEURE DE L'EST, DU LUNDI AU VENDREDI).
				</p>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					11. Conséquences d'une violation
				</h2>
				<p class="mb-2">
					Toute violation des présentes Conditions d'utilisation ou des politiques du Ministère peut
					entraîner des mesures disciplinaires, conformément à la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=32603"
						>Politique sur les services et le numérique</a
					>, à la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=32611"
						>Directive sur la gestion de la sécurité</a
					>, à la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=32601"
						>Directive sur les services et le numérique</a
					>, à la
					<a
						class="underline"
						target="_blank"
						rel="noopener noreferrer"
						href="https://www.tbs-sct.canada.ca/pol/doc-fra.aspx?id=17151"
						>Cadre stratégique sur la gestion de la conformité</a
					>. Les conséquences peuvent inclure la suspension ou la résiliation de votre accès à
					CANChat, selon ce que votre ministère jugera approprié.
				</p>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					12. Modifications des conditions
				</h2>
				<p class="mb-2">
					Services partagés Canada se réserve le droit de modifier ou de remplacer les présentes
					Conditions d'utilisation à tout moment. En continuant à accéder à CANChat ou à l'utiliser
					après l'entrée en vigueur de ces révisions, vous acceptez d'être lié par les conditions
					révisées.
				</p>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					13. Résiliation de l'accès
				</h2>
				<p class="mb-2">
					Services partagés Canada se réserve le droit de suspendre ou de résilier votre accès à
					CANChat à sa seule discrétion, en tout temps et pour quelque raison que ce soit, sans
					préavis, y compris, mais sans s'y limiter, les violations des présentes conditions
					d'utilisation, les préoccupations en matière de sécurité ou la nécessité opérationnelle.
					En cas de résiliation, votre accès à CANChat prendra fin.
				</p>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					14. Signalement d'une mauvaise utilisation, d'incidents ou de préoccupations
				</h2>
				<p class="mb-2">
					Si vous prenez connaissance de vulnérabilités en matière de sécurité, de cas où CANChat
					génère du contenu nuisible ou inapproprié, de violations potentielles des conditions
					d'utilisation ou de toute autre préoccupation (particulièrement en ce qui a trait au
					traitement des données Protégé B), veuillez les signaler immédiatement à <a
						class="underline"
						href="mailto:ssc.canchat-support-assistance-canchat.spc@ssc-spc.gc.ca"
						>Assistance CANChat</a
					>.
				</p>
			</div>

			<div class="mb-5">
				<h2 class="text-xl font-semibold mb-3 text-purple-700 dark:text-purple-400">
					15. Coordonnées
				</h2>
				<p class="mb-2">
					Pour toute question générale concernant CANChat ou les Conditions d'utilisation, veuillez
					contacter :
				</p>
				<ul class="list-disc pl-6">
					<li class="mb-2">
						<a
							class="underline"
							href="mailto:dsaiclientengagement.sdiaclientmobilisation@ssc-spc.gc.ca"
							>Intégration opérationnelle et mobilisation des clients</a
						>
					</li>
					<li class="mb-2">
						<a
							class="underline"
							target="_blank"
							rel="noopener noreferrer"
							href="https://gcxgce.sharepoint.com/teams/1000538/SitePages/fr/GC-AI-Hub.aspx"
							>Centre d'excellence en intelligence artificielle</a
						>
					</li>
				</ul>
			</div>

			<div class="mt-3">
				<p class="font-semibold">
					En acceptant d'utiliser CANChat, vous comprenez vos responsabilités et acceptez de
					respecter ces Conditions d'utilisation.
				</p>
			</div>
		</div>

		<div class="flex justify-center">
			<button
				on:click={handleAccept}
				disabled={accepting}
				class="px-4 py-2 mt-3 bg-purple-800 text-white p-4 rounded-md hover:bg-purple-800/80 disabled:opacity-50 transition-colors"
			>
				{#if accepting}
					Mise à jour...
				{:else}
					J'accepte les conditions
				{/if}
			</button>
		</div>
	</div>
</div>
