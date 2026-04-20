function toggleUserMenu(){

    const menu = document.getElementById("userDropdown")
    
    menu.style.display =
    menu.style.display === "block"
    ? "none"
    : "block"
    
    }
    
    window.onclick = function(event){
    
    if(!event.target.matches('.avatar')){
    
    let dropdown =
    document.getElementById("userDropdown")
    
    if(dropdown.style.display === "block"){
    
    dropdown.style.display = "none"
    
    }
    
    }
    
    }